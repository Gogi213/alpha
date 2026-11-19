#!/usr/bin/env python3
"""TK-531: разбор tcpdump к pingprobe.py — на пустых соединениях (нет данных, только наши пинги) время от отправки пинга до TCP-ACK биржи и до pong.
Запуск: tcpdump -nn -tt -r <pcap> | pingpcap.py <probe.json>   (захват `-i any` или на интерфейсе)   (probe.json — выход pingprobe.py)
Исходящий сегмент с данными после рукопожатия = пинг; ACK = первый входящий пакет с ack ≥ конца сегмента; pong = первый входящий сегмент с данными. Все времена — часы этого хоста (одни и те же для ACK и pong).
"""
import json
import re
import sys

LINE = re.compile(r"^(\d+\.\d+) (?:\S+\s+(?:In|Out)\s+)?IP (\S+)\.(\d+) > (\S+)\.(\d+): Flags \[([^\]]*)\], (?:seq (\d+)(?::(\d+))?, )?(?:ack (\d+), )?.*length (\d+)")


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def main():
    probe = json.load(open(sys.argv[1]))
    idle = {c["port"]: c for c in probe["conns"] if not c["subscribed"]}
    ev = {p: [] for p in idle}  # порт -> [(t, направление, seq_end, ack, len)]
    for ln in sys.stdin:
        m = LINE.match(ln)
        if not m:
            continue
        t, _, sp, _, dp, _, seq, seq_end, ack, length = m.groups()
        t, sp, dp, length = float(t), int(sp), int(dp), int(length)
        if dp == 443 and sp in ev:
            ev[sp].append((t, "out", int(seq_end or seq or 0) if length else 0, int(ack or 0), length))
        elif sp == 443 and dp in ev:
            ev[dp].append((t, "in", 0, int(ack or 0), length))
    out = {}
    for port, c in idle.items():
        e = sorted(ev[port])
        sends = [x for x in e if x[1] == "out" and x[4] > 0][4:]  # первые сегменты — TLS и upgrade
        acks, pongs, apps = [], [], []
        for i, (ts, _, end, _, _) in enumerate(sends):
            nxt = sends[i + 1][0] if i + 1 < len(sends) else ts + 5
            a = next((x[0] for x in e if x[1] == "in" and x[0] > ts and x[3] >= end), None)
            p = next((x[0] for x in e if x[1] == "in" and x[0] > ts and x[4] > 0), None)
            if a and a < nxt:
                acks.append((a - ts) * 1000)
            if p and p < nxt:
                pongs.append((p - ts) * 1000)
        out[port] = {"pings_seen": len(sends), "ack_ms_p50": pct(acks, .5), "ack_ms_min": min(acks, default=None), "ack_ms_max": max(acks, default=None),
                     "pong_ms_p50": pct(pongs, .5), "pong_ms_min": min(pongs, default=None), "pong_ms_max": max(pongs, default=None),
                     "app_ping_ms_p50": c["ping_p50"], "app_ping_ms_min": c["ping_min"], "kernel_rtt_p50": c["kernel_rtt_p50"], "rtt_connect_ms": c["rtt_connect_ms"]}
    print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main()
