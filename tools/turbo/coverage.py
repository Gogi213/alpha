#!/usr/bin/env python3
"""G-022 turbo: cold-start coverage of a collector run, from its own event files (`events-<shard>.jsonl`).

For every shard: when the first connection came up (seconds from process start = `pool-<day>.json` ts_ms, written right before the shards are
spawned), and how long the shard had NO live session afterwards inside the window (session model: `connected` opens, the first later `closed` /
`error` / `path_rejected` of that session or `rotated` / `path_switch` / `path_upgrade` naming it as `old_session` closes). Also the event counters that
show where the dial budget went. Usage: coverage.py <root> [--window-s 180] [--out coverage.json]
"""
import argparse
import glob
import json
import os
import statistics
from collections import Counter

ENDS = {"closed", "error", "path_rejected"}
OLD = {"rotated", "path_switch", "path_upgrade"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("root")
    ap.add_argument("--window-s", type=float, default=180.0)
    ap.add_argument("--t0", type=int, default=0, help="process start, ms (default: ts_ms of pool-<day>.json)")
    ap.add_argument("--out")
    a = ap.parse_args()
    t0 = a.t0
    if not t0:
        pools = sorted(glob.glob(os.path.join(a.root, "pool-*.json")))
        t0 = json.load(open(pools[0]))["ts_ms"]
    t1 = t0 + int(a.window_s * 1000)
    shards, total = {}, Counter()
    for p in sorted(glob.glob(os.path.join(a.root, "events-*.jsonl"))):
        name = os.path.basename(p)[len("events-"):-len(".jsonl")]
        evs = []
        for ln in open(p, encoding="utf-8"):
            try:
                evs.append(json.loads(ln))
            except ValueError:
                pass
        evs.sort(key=lambda e: e["ts_ms"])
        live, first, down_ms, last_t, cnt = set(), None, 0, None, Counter()
        for e in evs:
            ts = e["ts_ms"]
            if ts > t1:
                break
            ev = e["ev"]
            cnt[ev] += 1
            if ev == "error" and "502" in str(e.get("err", "")):
                cnt["error_502"] += 1
            before = len(live)
            if ev == "connected":
                live.add(e["session"])
                if first is None:
                    first = ts
            elif ev in ENDS:
                live.discard(e.get("session"))
            elif ev in OLD:
                live.discard(e.get("old_session"))
            if first is not None:
                if last_t is not None and before == 0:
                    down_ms += ts - last_t
                last_t = ts
        if first is not None and last_t is not None and not live:
            down_ms += t1 - last_t
        shards[name] = {"first_connected_s": None if first is None else round((first - t0) / 1000, 1), "uncovered_after_first_s": round(down_ms / 1000, 1),
                        "events": dict(cnt)}
        total.update(cnt)
    firsts = sorted(v["first_connected_s"] for v in shards.values() if v["first_connected_s"] is not None)
    n = len(shards)
    out = {
        "root": a.root, "t0_ms": t0, "window_s": a.window_s, "shards": n, "shards_connected": len(firsts),
        "first_connected_s": {"min": firsts[0] if firsts else None, "median": statistics.median(firsts) if firsts else None, "max": firsts[-1] if firsts else None},
        "covered_by_s": {str(s): sum(1 for f in firsts if f <= s) for s in (2, 5, 10, 15, 30, 60, 120, 180)},
        "uncovered_after_first_s_total": round(sum(v["uncovered_after_first_s"] for v in shards.values()), 1),
        "uncovered_after_first_s_max": max((v["uncovered_after_first_s"] for v in shards.values()), default=0),
        "events_total": dict(total),
        "per_shard": shards,
    }
    if a.out:
        json.dump(out, open(a.out, "w"), indent=1)
    keep = {k: v for k, v in out.items() if k != "per_shard"}
    print(json.dumps(keep))


if __name__ == "__main__":
    main()
