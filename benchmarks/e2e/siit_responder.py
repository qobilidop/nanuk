#!/usr/bin/env python3
"""Userspace IPv6 echo responder for the SIIT SimBricks scenario.

The SimBricks base guest kernel (linux-5.15.93) is built with
`# CONFIG_IPV6 is not set` -- it has no IPv6 stack at all (no
/proc/sys/net/ipv6, `ip -6 addr add` returns "Operation not supported").
So the "v6-only guest" cannot answer IPv6 pings through its kernel.

It can still speak IPv6 *on the wire*, though: an AF_PACKET raw socket works
at layer 2 and needs no kernel IP stack. This script binds one on the guest's
NIC, watches for the ICMPv6 echo requests the Nanuk SIIT core produces
(EtherType 0x86DD, next-header 58, type 128) addressed to our v6 address, and
crafts the matching echo reply (type 129, addresses + MACs swapped, ICMPv6
checksum recomputed over the RFC 4443 pseudo-header). The reply rides back
through the same core, is translated v6->v4, and reaches the v4 guest -- so
`ping` gets real answers across the address-family boundary.

Non-ICMPv6 traffic is received and classified, not answered: the forward
(v4->v6) path is still exercised for real, we just don't reply. For UDP the
responder logs each datagram's iperf application sequence number (iperf2
puts a signed 32-bit sequence in the first 4 payload bytes; negative marks
the FIN/close datagram) and keeps unique-vs-duplicate counts -- that is the
receiver-side ground truth beat 2 reconciles against: distinct sequence
numbers were *sent* by iperf, repeated ones were duplicated somewhere below
it on the path.

Usage: python3 siit_responder.py <ifname> <our-v6-addr>
"""

import socket
import struct
import sys

IFACE = sys.argv[1] if len(sys.argv) > 1 else "eth0"
OUR6_STR = sys.argv[2] if len(sys.argv) > 2 else "2001:db8:1::c001"
OUR6 = socket.inet_pton(socket.AF_INET6, OUR6_STR)  # pure userspace parse

ETH_P_ALL = 0x0003
ETH_P_IPV6 = 0x86DD
NH_UDP = 17
NH_ICMPV6 = 58
ICMP6_ECHO_REQUEST = 128
ICMP6_ECHO_REPLY = 129


def cksum16(data: bytes) -> int:
    if len(data) % 2:
        data += b"\x00"
    total = 0
    for i in range(0, len(data), 2):
        total += (data[i] << 8) | data[i + 1]
    while total >> 16:
        total = (total & 0xFFFF) + (total >> 16)
    return (~total) & 0xFFFF


def main() -> None:
    s = socket.socket(socket.AF_PACKET, socket.SOCK_RAW, socket.htons(ETH_P_ALL))
    s.bind((IFACE, 0))
    sys.stderr.write(f"siit_responder: listening on {IFACE} as {OUR6_STR}\n")
    sys.stderr.flush()
    replies = 0
    udp_frames = 0
    udp_seqs = {}  # iperf seq -> times seen
    while True:
        frame = s.recv(65535)
        if len(frame) < 54:
            continue
        eth_dst, eth_src, etype = frame[0:6], frame[6:12], frame[12:14]
        if etype != struct.pack("!H", ETH_P_IPV6):
            continue
        ip = frame[14:]
        if len(ip) < 40:
            continue
        if ip[6] == NH_UDP:
            # Classify, don't answer: log the iperf application sequence
            # number so the run log carries per-datagram receiver truth.
            udp_frames += 1
            payload = ip[48:]
            if len(payload) >= 4:
                seq = struct.unpack("!i", payload[0:4])[0]
                udp_seqs[seq] = udp_seqs.get(seq, 0) + 1
                dups = udp_frames - len(udp_seqs)
                pos = [k for k in udp_seqs if k > 0]
                # pos=<received>/<max> -- equal iff the paced data datagrams
                # (positive seqs, 1..max) all arrived, none lost, none dup'd.
                sys.stderr.write(
                    f"siit_responder: UDP #{udp_frames} len={len(frame)}"
                    f" dport={struct.unpack('!H', ip[42:44])[0]} seq={seq}"
                    f" seen={udp_seqs[seq]}"
                    f" | unique={len(udp_seqs)} dups={dups}"
                    f" pos={len(pos)}/{max(pos) if pos else 0}\n"
                )
                sys.stderr.flush()
            continue
        if ip[6] != NH_ICMPV6:
            continue
        src, dst = ip[8:24], ip[24:40]
        if dst != OUR6:
            continue
        icmp = ip[40:]
        if len(icmp) < 4 or icmp[0] != ICMP6_ECHO_REQUEST:
            continue

        # Echo reply: type 129, same id/seq/payload, checksum recomputed.
        body = icmp[4:]  # id + seq + data (everything past type/code/checksum)
        reply_icmp = bytes([ICMP6_ECHO_REPLY, 0]) + b"\x00\x00" + body
        plen = len(reply_icmp)
        r_src, r_dst = dst, src  # our address -> the requester
        pseudo = r_src + r_dst + struct.pack("!I", plen) + b"\x00\x00\x00" + bytes([NH_ICMPV6])
        ck = cksum16(pseudo + reply_icmp)
        reply_icmp = reply_icmp[0:2] + struct.pack("!H", ck) + reply_icmp[4:]

        ip6 = (
            b"\x60\x00\x00\x00"          # version 6, TC 0, flow label 0
            + struct.pack("!H", plen)     # payload length
            + bytes([NH_ICMPV6, 64])      # next header 58, hop limit 64
            + r_src
            + r_dst
        )
        out = eth_src + eth_dst + struct.pack("!H", ETH_P_IPV6) + ip6 + reply_icmp
        s.send(out)
        replies += 1
        if replies <= 16 or replies % 100 == 0:
            sys.stderr.write(f"siit_responder: echo reply #{replies}\n")
            sys.stderr.flush()


if __name__ == "__main__":
    main()
