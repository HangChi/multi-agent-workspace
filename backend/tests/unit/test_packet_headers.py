"""Actual header decoder tests: no sockets, root, payload execution or DB."""

from __future__ import annotations

import importlib.util
import ipaddress
import struct
from pathlib import Path

import pytest

from app.executors.egress_network import _protocol_guard, parse_packet_output

path = Path(__file__).resolve().parents[3] / "deploy" / "sbx-net" / "packet_capture.py"
spec = importlib.util.spec_from_file_location("packet_headers", path)
headers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(headers)


def ipv4(
    *, protocol=6, frag=0, payload=b"", source="172.30.0.3", target="93.184.216.34"
):
    ethernet = b"\0" * 12 + struct.pack("!H", 0x800)
    ip = struct.pack(
        "!BBHHHBBH4s4s",
        0x45,
        0,
        40 + len(payload),
        1,
        frag,
        64,
        protocol,
        0,
        ipaddress.ip_address(source).packed,
        ipaddress.ip_address(target).packed,
    )
    tcp = struct.pack("!HHIIBBHHH", 41001, 443, 123, 0, 0x50, 2, 1024, 0, 0)
    return ethernet + ip + tcp + payload


def ipv6(*, protocol=6, payload=b"", source="2001:db8::3", target="2606:4700::1111"):
    ethernet = b"\0" * 12 + struct.pack("!H", 0x86DD)
    ip = struct.pack(
        "!IHBB16s16s",
        6 << 28,
        20 + len(payload),
        protocol,
        64,
        ipaddress.ip_address(source).packed,
        ipaddress.ip_address(target).packed,
    )
    tcp = struct.pack("!HHIIBBHHH", 41001, 443, 123, 0, 0x50, 2, 1024, 0, 0)
    return ethernet + ip + tcp + payload


def test_tcp_header_keeps_tuple_initial_sequence_and_timestamp_never_payload():
    line = headers.decode(ipv4(payload=b"SECRET-DO-NOT-LOG"), {"172.30.0.3"}, 123.456)
    assert "SECRET" not in line
    assert "seq 123" in line and "Flags [S]" in line
    record = parse_packet_output(line, source_ip="172.30.0.3")[0]
    assert record["source_port"] == 41001 and record["port"] == 443
    assert record["target_ip"] == "93.184.216.34"
    assert record["occurred_at"] == "1970-01-01T00:02:03.456000+00:00"


@pytest.mark.parametrize("frag", [0x2000, 1, 0x2001])
def test_first_and_noninitial_ipv4_fragments_emit_denied_target_facts(frag):
    line = headers.decode(ipv4(frag=frag), {"172.30.0.3"}, 1)
    record = parse_packet_output(line, source_ip="172.30.0.3")[0]
    assert (
        record["action"] == "deny"
        and record["protocol"] == "fragment"
        and record["port"] == 0
    )
    assert record["target_ip"] == "93.184.216.34"


def test_dont_fragment_bit_alone_is_not_a_fragment():
    line = headers.decode(ipv4(frag=0x4000), {"172.30.0.3"}, 1)
    assert "Flags [S]" in line and "AW-DENY" not in line


@pytest.mark.parametrize("protocol", [0, 43, 44, 50, 51, 60, 135])
def test_ipv6_extension_and_fragment_paths_are_attributable_denied(protocol):
    line = headers.decode(ipv6(protocol=protocol), {"2001:db8::3"}, 1)
    record = parse_packet_output(line, source_ip="2001:db8::3")[0]
    assert (
        record["protocol"] == "extension"
        and record["action"] == "deny"
        and record["port"] == 0
    )
    assert record["target_ip"] == "2606:4700::1111"


@pytest.mark.parametrize(
    "protocol,name", [(1, "icmp"), (47, "ip-47"), (50, "ip-50"), (132, "ip-132")]
)
def test_non_tcp_udp_ipv4_protocols_never_silently_disappear(protocol, name):
    line = headers.decode(ipv4(protocol=protocol), {"172.30.0.3"}, 1)
    record = parse_packet_output(line, source_ip="172.30.0.3")[0]
    assert (
        record["protocol"] == name
        and record["action"] == "deny"
        and record["port"] == 0
    )


def test_ipv6_icmp_is_observed_as_denied_for_routed_egress():
    line = headers.decode(ipv6(protocol=58), {"2001:db8::3"}, 1)
    record = parse_packet_output(line, source_ip="2001:db8::3")[0]
    assert record["protocol"] == "icmp6" and record["port"] == 0


def test_udp_headers_keep_source_and_destination_ports():
    line = headers.decode(ipv4(protocol=17), {"172.30.0.3"}, 1)
    record = parse_packet_output(line, source_ip="172.30.0.3")[0]
    assert (
        record["protocol"] == "udp"
        and record["source_port"] == 41001
        and record["port"] == 443
    )


def test_actual_execution_source_is_required():
    assert headers.decode(ipv4(source="172.30.0.4"), {"172.30.0.3"}, 1) is None
    assert headers.decode(ipv6(source="2001:db8::4"), {"2001:db8::3"}, 1) is None


@pytest.mark.parametrize(
    "frame",
    [b"", b"\0" * 13, b"\0" * 12 + b"\x08\0", b"\0" * 12 + b"\x86\xdd" + b"\0" * 39],
)
def test_truncated_frames_do_not_fabricate_addresses(frame):
    assert headers.decode(frame, {"172.30.0.3", "2001:db8::3"}, 1) is None


def test_fragment_guard_must_precede_kernel_defragmentation():
    script = _protocol_guard("f4e784e5-1dd9-4ae1-84ad-a0c66bdb761a", "br-abc123")
    assert "hook prerouting priority -450" in script
    assert "hook forward priority -20" in script
    assert "frag-off & 0x3fff != 0 drop" in script
    assert "meta l4proto != { tcp, udp } drop" in script


def test_header_decoder_supports_vlan_without_inspecting_payload():
    raw = ipv4()
    vlan = raw[:12] + b"\x81\0\0\x01\x08\0" + raw[14:]
    assert "93.184.216.34.443" in headers.decode(vlan, {"172.30.0.3"}, 1)
