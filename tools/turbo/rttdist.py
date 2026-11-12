#!/usr/bin/env python3
"""G-022 turbo: handshake-RTT distribution of the connections a collector run opened (from `events-<shard>.jsonl`).
Per run window [since-ms, until-ms]: accepted connections (`connected`), rejected paths (`path_rejected_rtt`), paths accepted although
slower than the cutoff because the budget reserve was reached (`path_slow_accepted`), and the events that follow a bad path.
Usage: rttdist.py <root> --since-ms T0 [--until-ms T1] [--cutoff 300] [--out rttdist.json]
"""
import argparse
import glob
import json
import os
from collections import Counter, defaultdict


def pct(xs, p):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))] if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--since-ms", type=int, default=0)
    ap.add_argument("--until-ms", type=int, default=1 << 62)
    ap.add_argument("--cutoff", type=float, default=300.0)
    ap.add_argument("--out")
    a = ap.parse_args()
    acc, rej, slow = [], [], []
    ev = Counter()
    per_shard = defaultdict(list)
    for p in sorted(glob.glob(os.path.join(a.root, "events-*.jsonl"))):
        shard = os.path.basename(p)[len("events-"):-len(".jsonl")]
        for ln in open(p, encoding="utf-8", errors="replace"):
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            ts = e.get("ts_ms") or e.get("ts") or 0
            if not (a.since_ms <= ts <= a.until_ms):
                continue
            k = e.get("ev") or e.get("event") or e.get("type")
            ev[k] += 1
            r = e.get("rtt_ms")
            if k == "connected" and r is not None:
                acc.append(r)
                per_shard[shard].append((e.get("session"), r))
            elif k == "path_rejected_rtt" and r is not None:
                rej.append(r)
            elif k == "path_slow_accepted" and r is not None:
                slow.append(r)

    def dist(xs):
        return {"n": len(xs), "p10": pct(xs, 0.1), "p50": pct(xs, 0.5), "p90": pct(xs, 0.9), "max": max(xs, default=None),
                "over_cutoff": sum(1 for x in xs if x > a.cutoff)}

    out = {"cutoff_ms": a.cutoff, "accepted": dist(acc), "rejected": dist(rej), "slow_accepted": dist(slow),
           "events": {k: v for k, v in ev.items() if k in ("connected", "path_rejected_rtt", "path_slow_accepted", "timeout", "ping_degraded",
                                                           "path_switch", "switch_failed", "error", "gap", "connect_wait", "rotated", "entrance_switch")},
           "per_shard_connected": {s: v[:4] for s, v in sorted(per_shard.items())}}
    print(json.dumps({k: out[k] for k in ("cutoff_ms", "accepted", "rejected", "slow_accepted", "events")}))
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)


if __name__ == "__main__":
    main()
