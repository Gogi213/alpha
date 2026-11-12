#!/usr/bin/env python3
"""G-022 turbo: handshake RTT per destination IP x source port (route classes of TK-527).
Resolves the host several times (DNS gives a rotating subset), then connects (TCP only, no TLS) to every IP from a fixed set of source
ports, interleaved over passes; the kernel `tcpi_rtt` right after connect is the handshake RTT (as in the collector's dial). The socket is closed
with RST (SO_LINGER 0) so a source port can be reused at once. Each connect counts against the exchange's per-IP connection limit — keep the total
below ~280 per 5 min and stop the collector on this host while probing.
Usage: iprtt.py <host> --out iprtt.json [--ports 6] [--passes 2] [--gap 0.7] [--family 4|6|any]
"""
import argparse
import json
import random
import socket
import struct
import time


def kernel_rtt_ms(s):
    try:
        return struct.unpack_from("I", s.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104), 68)[0] / 1000
    except (AttributeError, OSError):
        return None


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("host")
    ap.add_argument("--out", required=True)
    ap.add_argument("--ports", type=int, default=6)
    ap.add_argument("--passes", type=int, default=2)
    ap.add_argument("--gap", type=float, default=0.7)
    ap.add_argument("--family", default="4")
    ap.add_argument("--port", type=int, default=443)
    ap.add_argument("--cutoff", type=float, default=300.0)
    a = ap.parse_args()
    fam = {"4": socket.AF_INET, "6": socket.AF_INET6, "any": 0}[a.family]
    ips = {}
    for _ in range(8):
        try:
            for r in socket.getaddrinfo(a.host, a.port, family=fam, type=socket.SOCK_STREAM):
                ips[r[4][0]] = r[0]
        except OSError:
            pass
        time.sleep(0.4)
    ips = sorted(ips.items())
    rnd = random.Random(1)
    ports = sorted(rnd.sample(range(41000, 59000), a.ports))
    rows = []
    t_start = time.time()
    for ps in range(a.passes):
        for port in ports:
            for ip, af in ips:
                s = socket.socket(af, socket.SOCK_STREAM)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                s.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
                s.settimeout(6)
                row = {"t": round(time.time() - t_start, 1), "pass": ps, "ip": ip, "src_port": port}
                try:
                    s.bind(("::" if af == socket.AF_INET6 else "0.0.0.0", port))
                    t0 = time.monotonic()
                    s.connect((ip, a.port))
                    row["connect_ms"] = round((time.monotonic() - t0) * 1000, 1)
                    row["rtt_ms"] = kernel_rtt_ms(s)
                except OSError as e:
                    row["err"] = str(e)[:60]
                s.close()
                rows.append(row)
                time.sleep(a.gap)
    per_ip = {}
    for ip, _ in ips:
        xs = [r["rtt_ms"] for r in rows if r["ip"] == ip and r.get("rtt_ms")]
        per_ip[ip] = {"n": len(xs), "p10": pct(xs, 0.1), "p50": pct(xs, 0.5), "p90": pct(xs, 0.9), "max": max(xs, default=None),
                      "fast_pct": round(100 * sum(1 for x in xs if x <= a.cutoff) / len(xs)) if xs else None}
    per_port = {}
    for p in ports:
        xs = [r["rtt_ms"] for r in rows if r["src_port"] == p and r.get("rtt_ms")]
        per_port[p] = {"n": len(xs), "p50": pct(xs, 0.5), "fast_pct": round(100 * sum(1 for x in xs if x <= a.cutoff) / len(xs)) if xs else None}
    # the same (ip, port) pair twice: does the class repeat?
    pair = {}
    for r in rows:
        if r.get("rtt_ms"):
            pair.setdefault((r["ip"], r["src_port"]), []).append(r["rtt_ms"])
    rep = [v for v in pair.values() if len(v) >= 2]
    same_class = sum(1 for v in rep if (v[0] <= a.cutoff) == (v[1] <= a.cutoff))
    allr = [r["rtt_ms"] for r in rows if r.get("rtt_ms")]
    out = {"host": a.host, "cutoff_ms": a.cutoff, "ips": [i for i, _ in ips], "ports": ports, "connects": len(rows),
           "errors": sum(1 for r in rows if "err" in r),
           "all": {"n": len(allr), "p10": pct(allr, 0.1), "p50": pct(allr, 0.5), "p90": pct(allr, 0.9), "max": max(allr, default=None),
                   "fast_pct": round(100 * sum(1 for x in allr if x <= a.cutoff) / len(allr)) if allr else None},
           "per_ip": per_ip, "per_port": per_port,
           "pair_repeat": {"pairs_with_2_samples": len(rep), "same_class": same_class}, "rows": rows}
    json.dump(out, open(a.out, "w"), indent=1)
    print(json.dumps({k: out[k] for k in ("host", "ips", "connects", "errors", "all", "per_ip", "per_port", "pair_repeat")}))


if __name__ == "__main__":
    main()
