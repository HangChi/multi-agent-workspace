"""Bounded, payload-free AF_PACKET IP-header observer for one execution bridge.

Unlike human tcpdump output this preserves fragment/extension denial facts.
Only 128 header bytes are read; no packet payload is logged or persisted. Source
IP filtering is execution-private. Missing final statistics, kernel loss, malformed
headers and Docker log rollover cause collection failure, never fake completeness.
"""
from __future__ import annotations

import ctypes
import ipaddress
import os
import signal
import socket
import struct
import sys
import time

SNAPLEN = 128
SOL_PACKET = 263
PACKET_STATISTICS = 6
SO_TIMESTAMPNS = 35  # Linux 64-bit timespec SCM_TIMESTAMPNS


def receive(sock: socket.socket) -> tuple[bytes, float, str, int]:
    frame, ancillary, _, address = sock.recvmsg(SNAPLEN, 128)
    for level, kind, data in ancillary:
        if level == socket.SOL_SOCKET and kind == SO_TIMESTAMPNS:
            seconds, nanos = struct.unpack_from("@ll", data)
            return frame, seconds + nanos / 1_000_000_000, address[0], address[2]
    raise RuntimeError("kernel packet timestamp missing; cannot prove audit timing")


def source_filter(sources: set[str]) -> list[tuple[int, int, int, int]]:
    """Classic BPF checks only this execution's source, including VLAN tags.

    Attach before readiness. The kernel rejects unrelated executions' packets
    before copying them into this socket's finite buffer or userspace process.
    """
    instructions = []
    labels = {}

    def label(value):
        labels[value] = len(instructions)

    def emit(code, value, yes=None, no=None):
        instructions.append((code, value, yes, no))

    v4 = next((int(ipaddress.ip_address(s)) for s in sources if ":" not in s), None)
    v6 = next((ipaddress.ip_address(s).packed for s in sources if ":" in s), None)
    for tag in range(3):
        label(f"ether{tag}")
        emit(0x28, 12 + tag * 4)  # BPF_LD | BPF_H | BPF_ABS
        emit(0x15, 0x0800, f"v4_{tag}")
        emit(0x15, 0x86DD, f"v6_{tag}")
        if tag < 2:
            emit(0x15, 0x8100, f"ether{tag + 1}")
            emit(0x15, 0x88A8, f"ether{tag + 1}", "drop")
        else:
            emit(0x05, "drop")  # BPF_JMP | BPF_JA
    for tag in range(3):
        label(f"v4_{tag}")
        if v4 is None:
            emit(0x05, "drop")
        else:
            emit(0x20, 26 + tag * 4)  # BPF_LD | BPF_W | BPF_ABS
            emit(0x15, v4, "accept", "drop")
        label(f"v6_{tag}")
        if v6 is None:
            emit(0x05, "drop")
        else:
            for word in range(4):
                emit(0x20, 22 + tag * 4 + word * 4)
                emit(0x15, int.from_bytes(v6[word * 4:word * 4 + 4]), None, "drop")
            emit(0x05, "accept")
    label("accept")
    emit(0x06, SNAPLEN)  # BPF_RET | BPF_K
    label("drop")
    emit(0x06, 0)
    result = []
    for index, (code, value, yes, no) in enumerate(instructions):
        if code == 0x05:
            value = labels[value] - index - 1
        jt = labels[yes] - index - 1 if yes else 0
        jf = labels[no] - index - 1 if no else 0
        if min(jt, jf) < 0 or max(jt, jf) > 255:
            raise RuntimeError("invalid fixed source BPF program")
        result.append((code, jt, jf, value))
    return result


def attach_filter(sock: socket.socket, sources: set[str]) -> None:
    class Instruction(ctypes.Structure):
        _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                    ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

    class Program(ctypes.Structure):
        _fields_ = [("length", ctypes.c_ushort), ("filter", ctypes.POINTER(Instruction))]

    rows = source_filter(sources)
    array = (Instruction * len(rows))(*(Instruction(*row) for row in rows))
    program = Program(len(rows), array)
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.setsockopt(sock.fileno(), socket.SOL_SOCKET, 26, ctypes.byref(program), ctypes.sizeof(program)) != 0:
        raise OSError(ctypes.get_errno(), "cannot install execution source BPF")


def execution_ingress(interface: str, packet_type: int, bridge: str, cache: dict[str, bool]) -> bool:
    if packet_type == 4 or interface == bridge:  # PACKET_OUTGOING: avoid forwarded duplicates
        return False
    if interface not in cache:
        if len(cache) >= 32:
            raise RuntimeError("execution interface observation budget exhausted")
        try:
            cache[interface] = os.path.basename(os.readlink(f"/sys/class/net/{interface}/master")) == bridge
        except FileNotFoundError:
            # The interface could have disappeared while the sandbox stopped.
            cache[interface] = False
    return cache[interface]


def decode(frame: bytes, sources: set[str], timestamp: float) -> str | None:
    if len(frame) < 14:
        return None
    ether = struct.unpack_from("!H", frame, 12)[0]
    offset = 14
    for _ in range(2):
        if ether not in (0x8100, 0x88A8):
            break
        if len(frame) < offset + 4:
            return None
        ether = struct.unpack_from("!H", frame, offset + 2)[0]
        offset += 4
    denial = None
    if ether == 0x0800:
        if len(frame) < offset + 20:
            return None
        header = (frame[offset] & 15) * 4
        source = str(ipaddress.IPv4Address(frame[offset + 12:offset + 16]))
        target = str(ipaddress.IPv4Address(frame[offset + 16:offset + 20]))
        protocol = frame[offset + 9]
        version = "IP"
        if frame[offset] >> 4 != 4 or header < 20:
            denial = "invalid"
        elif struct.unpack_from("!H", frame, offset + 6)[0] & 0x3FFF:
            denial = "fragment"
        offset += header
    elif ether == 0x86DD:
        if len(frame) < offset + 40:
            return None
        source = str(ipaddress.IPv6Address(frame[offset + 8:offset + 24]))
        target = str(ipaddress.IPv6Address(frame[offset + 24:offset + 40]))
        protocol = frame[offset + 6]
        version = "IP6"
        if (protocol == 58 and frame[offset + 7] == 255 and len(frame) > offset + 40
                and frame[offset + 40] in (135, 136)
                and (ipaddress.IPv6Address(target).is_link_local
                     or ipaddress.IPv6Address(target).is_multicast
                     or frame[offset + 8:offset + 16] == frame[offset + 24:offset + 32])):
            # Kernel-generated on-link neighbor discovery, not an external
            # connection. INPUT permits only these hop-limit-255 control types;
            # routed ICMP/ND still hits the independent FORWARD protocol guard.
            return None
        if frame[offset] >> 4 != 6:
            denial = "invalid"
        elif protocol not in (6, 17):
            denial = "icmp6" if protocol == 58 else "extension"
        offset += 40
    else:
        return None
    if source not in sources:
        return None
    if protocol not in (6, 17) and denial is None:
        denial = "icmp" if protocol == 1 else f"ip-{protocol}"
    if denial is not None:
        return f"{timestamp:.6f} {version} {source} > {target}: AW-DENY {denial}"
    if len(frame) < offset + (20 if protocol == 6 else 8):
        return f"{timestamp:.6f} {version} {source} > {target}: AW-DENY invalid"
    sport, dport = struct.unpack_from("!HH", frame, offset)
    if protocol == 6:
        sequence = struct.unpack_from("!I", frame, offset + 4)[0]
        flags = frame[offset + 13]
        letters = "".join(value for bit, value in ((1, "F"), (2, "S"), (4, "R"), (16, ".")) if flags & bit)
        detail = f"Flags [{letters}], seq {sequence}, length 0"
    else:
        detail = "UDP, length 0"
    return f"{timestamp:.6f} {version} {source}.{sport} > {target}.{dport}: {detail}"


def main() -> None:
    if len(sys.argv) not in (3, 4):
        raise ValueError("expected execution bridge and one/two source IPs")
    bridge = sys.argv[1]
    sources = {str(ipaddress.ip_address(x)) for x in sys.argv[2:]}
    sock = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(3))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 1 << 20)
    sock.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS, 1)
    # Observe execution veth ingress BEFORE bridge/netfilter defragmentation.
    # Binding only to the bridge would miss fragments dropped at priority -450.
    attach_filter(sock, sources)
    sock.settimeout(.1)
    running = True
    emitted = 0
    interfaces: dict[str, bool] = {}

    def stop(*_):
        nonlocal running
        running = False

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    print(f"listening on {bridge} (trusted IP headers only)", file=sys.stderr, flush=True)
    deadline = time.monotonic() + 660
    try:
        while running and time.monotonic() < deadline:
            try:
                frame, occurred_at, interface, packet_type = receive(sock)
            except TimeoutError:
                continue
            if not execution_ingress(interface, packet_type, bridge, interfaces):
                continue
            result = decode(frame, sources, occurred_at)
            if result is not None:
                print(result, flush=True)
                emitted += 1
                if emitted > 65536:
                    raise RuntimeError("execution packet audit budget exhausted")
        if time.monotonic() >= deadline:
            raise RuntimeError("execution packet observer lifetime exceeded")
        # Drain already-queued headers before publishing final counts.
        sock.setblocking(False)
        for _ in range(65536 - emitted):
            try:
                frame, occurred_at, interface, packet_type = receive(sock)
            except BlockingIOError:
                break
            if not execution_ingress(interface, packet_type, bridge, interfaces):
                continue
            result = decode(frame, sources, occurred_at)
            if result is not None:
                print(result, flush=True)
                emitted += 1
        received, dropped = struct.unpack_from("II", sock.getsockopt(SOL_PACKET, PACKET_STATISTICS, 12))
        print(f"{emitted} packets captured\n{received} packets received by filter\n{dropped} packets dropped by kernel",
              file=sys.stderr, flush=True)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
