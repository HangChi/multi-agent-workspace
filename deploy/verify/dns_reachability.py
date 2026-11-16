"""Read-only native DNS diagnostic; independent of the mandatory pypi.org case.

Run inside the own DNS network namespace and the host mediator namespace. Fixed
public addresses only; no configuration changes, raw packets, answers or errors
are exported. Each query has one absolute one-second deadline. A diagnostic
answer is not an acceptance result and never waives the application DNS gate.
"""
from __future__ import annotations

import ipaddress
import json
import socket
import struct
import sys
import time

RESOLVERS = {
    'configured_primary': '223.5.5.5',
    'configured_secondary': '119.29.29.29',
    'public_alternative_1': '1.1.1.1',
    'public_alternative_2': '8.8.8.8',
}
TRANSACTION_ID = 0xA002
QUESTION = (struct.pack('!6H', TRANSACTION_ID, 0x0100, 1, 0, 0, 0)
            + b'\x07example\x03com\x00\x00\x01\x00\x01')


def query(address: str, *, stream: bool) -> dict:
    started = time.monotonic()
    deadline = started + 1.0
    outcome = 'invalid_response'
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM if stream else socket.SOCK_DGRAM) as conn:
            conn.settimeout(1.0)
            conn.connect((address, 53))

            def budget() -> None:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError
                conn.settimeout(remaining)

            def read_exact(length: int) -> bytes:
                data = bytearray()
                while len(data) < length:
                    budget()
                    block = conn.recv(length - len(data))
                    if not block:
                        raise ValueError
                    data.extend(block)
                return bytes(data)

            budget()
            conn.sendall(struct.pack('!H', len(QUESTION)) + QUESTION if stream else QUESTION)
            if stream:
                length, = struct.unpack('!H', read_exact(2))
                if not 12 <= length <= 4096:
                    raise ValueError
                reply = read_exact(length)
            else:
                budget()
                reply = conn.recv(4096)
            if len(reply) >= 12:
                transaction, flags, questions, answers, _, _ = struct.unpack('!6H', reply[:12])
                if (transaction == TRANSACTION_ID and flags & 0x8000 and not flags & 0x020F
                        and questions == 1 and answers > 0):
                    outcome = 'answered'
    except TimeoutError:
        outcome = 'timeout'
    except OSError:
        outcome = 'socket_error'
    except ValueError:
        outcome = 'invalid_response'
    return {'outcome': outcome, 'elapsed_ms': round((time.monotonic() - started) * 1000)}


def probe(proxy_ip: str | None = None) -> list[dict]:
    targets = dict(RESOLVERS)
    if proxy_ip is not None:
        proxy = ipaddress.IPv4Address(proxy_ip)
        if not proxy.is_private or proxy.is_loopback or proxy.is_link_local or proxy.is_unspecified:
            raise ValueError('own proxy must have a private bridge IPv4 address')
        targets['own_shared_proxy'] = str(proxy)
    return [dict(resolver=name, transport='tcp' if stream else 'udp', **query(address, stream=stream))
            for name, address in targets.items() for stream in (False, True)]


if __name__ == '__main__':
    print(json.dumps(probe(sys.argv[1] if len(sys.argv) == 2 else None), separators=(',', ':')))
