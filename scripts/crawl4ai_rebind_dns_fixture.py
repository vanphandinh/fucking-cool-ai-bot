"""Disposable DNS fixture for the isolated Crawl4AI Docker test container.

Run inside *fcai-crawl4ai* on loopback 127.0.0.53, never on the host or VPS.
For each unique ``*.fcai.test`` name, answer the first A lookup with the
controlled public fixture, and all subsequent A lookups with the private sink.
TTL is zero. DNS queries and returned addresses are printed for verification.

The test only proves rebinding if the log records both answers for the exact
name requested through the real /crawl endpoint.
"""

from __future__ import annotations

import argparse
import ipaddress
import socket
import struct
from collections import defaultdict


def serve(bind: str, public_ip: str, private_ip: str, proxy_ip: str) -> None:
    counts: dict[str, int] = defaultdict(int)
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.bind((bind, 53))
    print(f"DNS fixture listening on {bind}:53", flush=True)
    while True:
        packet, peer = sock.recvfrom(2048)
        if len(packet) < 17 or packet[4:6] != b"\x00\x01":
            continue
        offset = 12
        labels: list[str] = []
        try:
            while True:
                label_length = packet[offset]
                offset += 1
                if not label_length:
                    break
                if label_length > 63 or offset + label_length > len(packet):
                    raise ValueError("unsupported DNS question")
                labels.append(packet[offset : offset + label_length].decode("ascii"))
                offset += label_length
            qtype, qclass = struct.unpack_from("!HH", packet, offset)
        except (ValueError, IndexError, UnicodeDecodeError, struct.error):
            continue
        hostname = ".".join(labels).lower()
        question = packet[12 : offset + 4]
        destination = None
        if qtype == 1 and qclass == 1:
            if hostname == "crawl4ai-proxy":
                destination = proxy_ip
            elif hostname.endswith(".fcai.test"):
                counts[hostname] += 1
                destination = public_ip if counts[hostname] == 1 else private_ip
        answers = 1 if destination else 0
        flags = 0x8180 if destination or qtype == 28 else 0x8183
        response = packet[:2] + struct.pack("!HHHHH", flags, 1, answers, 0, 0) + question
        if destination:
            response += b"\xc0\x0c" + struct.pack("!HHIH", 1, 1, 0, 4)
            response += ipaddress.IPv4Address(destination).packed
        sock.sendto(response, peer)
        print(f"DNS_QUERY host={hostname} type={qtype} answer={destination or '-'}", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bind", default="127.0.0.53")
    parser.add_argument("--public-ip", default="11.23.0.50")
    parser.add_argument("--private-ip", default="172.19.0.4")
    parser.add_argument("--proxy-ip", default="172.20.0.3")
    args = parser.parse_args()
    serve(args.bind, args.public_ip, args.private_ip, args.proxy_ip)
