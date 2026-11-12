#!/usr/bin/env python3
"""G-022 turbo: one 3-minute window of one or two Binance collector runs (same script for every window; baseline and A/B).

  abwin.py --run A=/path/rootA [--run B=/path/rootB] --t0 <ms> --t1 <ms> --out window.json

Per run (events of the run + records on disk with rx time in [t0, t1)):
  dials / fast dials (handshake RTT <= 300 ms) / fast share of dials, accepted sessions and their fast share, slow accepted, events that replace a session;
  book lag p50 / p99 / max (rx - E, after the writer's dedup), late = book records with lag > 2 s;
  coverage, book cells = (symbol, E-second) with >= 1 book record, E-second in [t0, t1), records seen up to 10 s after t1:
    cov_any    = cells of the run / cells of the union of all runs (any lag), cov_timely = same but only records with lag <= 2 s;
    book_given_trade = share of the run's trade cells (symbol, E-second) that also have a book record (absolute check, no second run needed).
"""
import argparse
import glob
import json
import os
import sys
from collections import Counter

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "collector"))
from accept_ws import count_percentile, read_lines  # noqa: E402
from wscore import codec  # noqa: E402

FAST_RTT_MS = 300
LATE_MS = 2000
TAIL_MS = 10_000


def events(root, t0, t1):
    ev = Counter()
    dials = fast = acc = acc_fast = 0
    for p in glob.glob(os.path.join(root, "events-*.jsonl")):
        for ln in open(p):
            try:
                e = json.loads(ln)
            except ValueError:
                continue
            if not t0 <= e["ts_ms"] < t1:
                continue
            ev[e["ev"]] += 1
            if e["ev"] in ("connected", "path_rejected_rtt"):
                dials += 1
                f = e.get("rtt_ms", 1e9) <= FAST_RTT_MS
                fast += f
                if e["ev"] == "connected":
                    acc += 1
                    acc_fast += f
    return {"dials": dials, "fast_dials": fast, "fast_share": round(fast / dials, 3) if dials else None, "accepted": acc, "accepted_fast": acc_fast,
            "accepted_fast_share": round(acc_fast / acc, 3) if acc else None, "events": dict(ev)}


def disk(root, t0, t1):
    lags, late, n = Counter(), 0, 0
    cells_any, cells_timely, trade_cells = set(), set(), set()
    s0, s1 = -(-t0 // 1000), t1 // 1000
    for p in sorted(glob.glob(os.path.join(root, "20*", "*.tsv.zst"))):
        if os.path.getmtime(p) * 1000 < t0:
            continue
        for r in codec.decode(read_lines(p)):
            rx = r["rx_ns"] // 1_000_000
            if r["t"] not in ("b", "t") or not t0 <= rx < t1 + TAIL_MS:
                continue
            sec = r["E"] // 1000
            lag = rx - r["E"]
            if r["t"] == "t":
                if s0 <= sec < s1:
                    trade_cells.add((r["s"], sec))
                continue
            if t0 <= rx < t1:
                lags[lag] += 1
                n += 1
                late += lag > LATE_MS
            if s0 <= sec < s1:
                cells_any.add((r["s"], sec))
                if lag <= LATE_MS:
                    cells_timely.add((r["s"], sec))
    return {"book_n": n, "lag_p50": count_percentile(lags, 0.5), "lag_p99": count_percentile(lags, 0.99), "lag_max": max(lags, default=None), "late": late,
            "late_share": round(late / n, 4) if n else None}, cells_any, cells_timely, trade_cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True, help="LABEL=root")
    ap.add_argument("--t0", type=int, required=True)
    ap.add_argument("--t1", type=int, required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    runs, cells = {}, {}
    for spec in a.run:
        label, root = spec.split("=", 1)
        d, any_, timely, trades = disk(root, a.t0, a.t1)
        runs[label] = {**events(root, a.t0, a.t1), **d}
        cells[label] = (any_, timely)
        runs[label]["book_given_trade"] = round(len(trades & any_) / len(trades), 4) if trades else None
        runs[label]["book_given_trade_timely"] = round(len(trades & cells[label][1]) / len(trades), 4) if trades else None
        runs[label]["trade_cells"] = len(trades)
    union = set().union(*(c[0] for c in cells.values()))
    for label, (any_, timely) in cells.items():
        runs[label]["cells_union"] = len(union)
        runs[label]["cov_any"] = round(len(any_) / len(union), 5) if union else None
        runs[label]["cov_timely"] = round(len(timely) / len(union), 5) if union else None
    with open(a.out, "w") as f:
        json.dump({"t0": a.t0, "t1": a.t1, "runs": runs}, f, indent=1)


if __name__ == "__main__":
    main()
