#!/usr/bin/env python3
"""G-022 turbo: one 3-minute window of one or two Binance collector runs (same script for every window; baseline and A/B).

  abwin.py --run A=/path/rootA [--run B=/path/rootB] --t0 <ms> --t1 <ms> --out window.json

Per run (events of the run + records on disk with rx time in [t0, t1)):
  dials / fast dials (handshake RTT <= 300 ms) / fast share of dials, accepted sessions and their fast share, slow accepted, events that replace a session;
  book lag p50 / p99 / max (rx - E, after the writer's dedup), late = book records with lag > 2 s;
  coverage, book cells = (symbol, E-second) with >= 1 book record, E-second in [t0, t1), records seen up to 10 s after t1:
    cov_any    = cells of the run / cells of the union of all runs (any lag), cov_timely = same but only records with lag <= 2 s;
    book_given_trade = share of the run's trade cells (symbol, E-second) that also have a book record (absolute check, no second run needed);
  with --pool / --covered-ms (single-run steps): mark coverage = absolute — every pool symbol has a mark record every second (`!markPrice@arr@1s`), a gap > 1.5 s between
    consecutive records (from --covered-ms to t1) counts (gap - 1 s) missing seconds; mark_cov = 1 - missing / (symbols * seconds); 100 % means no gap.
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
    dials = fast = acc = acc_fast = drops = connect_errors = 0
    for p in glob.glob(os.path.join(root, "events-*.jsonl")):
        evs = []
        for ln in open(p):
            try:
                evs.append(json.loads(ln))
            except ValueError:
                pass
        evs.sort(key=lambda e: e["ts_ms"])
        live = set()  # sessions with a connection (a reconnect keeps the session number): `error` of a live one is a drop, otherwise a failed connect
        for e in evs:
            name = e["ev"]
            if name == "connected":
                live.add(e["session"])
            in_window = t0 <= e["ts_ms"] < t1
            if name == "error":
                if e.get("session") in live:
                    live.discard(e["session"])
                    drops += in_window
                else:
                    connect_errors += in_window
            elif name in ("closed", "path_rejected"):
                live.discard(e.get("session"))
            if not in_window:
                continue
            ev[name] += 1
            if name in ("connected", "path_rejected_rtt"):
                dials += 1
                f = e.get("rtt_ms", 1e9) <= FAST_RTT_MS
                fast += f
                if name == "connected":
                    acc += 1
                    acc_fast += f
    return {"dials": dials, "fast_dials": fast, "fast_share": round(fast / dials, 3) if dials else None, "accepted": acc, "accepted_fast": acc_fast,
            "accepted_fast_share": round(acc_fast / acc, 3) if acc else None, "drops": drops, "connect_errors": connect_errors, "events": dict(ev)}


def mark_gaps(marks, pool, covered, t1):
    """marks: symbol -> [E ms]; (missing seconds, symbols without any record, symbols with a gap)."""
    missing, none, gapped = 0.0, 0, 0
    for sym in pool:
        prev, miss = covered, 0.0
        for e in sorted(set(marks.get(sym, ()))):
            if e - prev > 1500:
                miss += e - prev - 1000
            prev = e
        if t1 - prev > 1500:
            miss += t1 - prev - 1000
        none += sym not in marks
        gapped += miss > 0
        missing += miss
    return missing / 1000, none, gapped


def disk(root, t0, t1, pool=None, covered=0):
    lags, late, late_cov, n = Counter(), 0, 0, 0
    cells_any, cells_timely, trade_cells = set(), set(), set()
    marks = {}
    s0, s1 = -(-t0 // 1000), t1 // 1000
    for p in sorted(glob.glob(os.path.join(root, "20*", "*.tsv.zst"))):
        if os.path.getmtime(p) * 1000 < t0:
            continue
        for r in codec.decode(read_lines(p)):
            rx = r["rx_ns"] // 1_000_000
            if r["t"] == "m" and pool and t0 <= rx < t1 + TAIL_MS and covered <= r["E"] < t1 and r["s"].upper() in pool:
                marks.setdefault(r["s"].upper(), []).append(r["E"])
                continue
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
                late_cov += lag > LATE_MS and r["E"] >= covered  # late records after the cold start (the first frame of a topic carries a stale state: lag up to ~3.5 s, rarely ~10 s)
            if s0 <= sec < s1:
                cells_any.add((r["s"], sec))
                if lag <= LATE_MS:
                    cells_timely.add((r["s"], sec))
    out = {"book_n": n, "lag_p50": count_percentile(lags, 0.5), "lag_p99": count_percentile(lags, 0.99), "lag_max": max(lags, default=None), "late": late, "late_after_covered": late_cov,
           "late_share": round(late / n, 4) if n else None}
    if pool:
        miss_s, none, gapped = mark_gaps(marks, pool, covered, t1)
        expected = len(pool) * max(1, t1 - covered) / 1000
        out.update(mark_symbols=len(pool), mark_missing_s=round(miss_s, 1), mark_symbols_without_records=none, mark_symbols_with_gap=gapped, mark_cov=round(1 - miss_s / expected, 6))
    return out, cells_any, cells_timely, trade_cells


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", action="append", required=True, help="LABEL=root")
    ap.add_argument("--t0", type=int, required=True)
    ap.add_argument("--t1", type=int, required=True)
    ap.add_argument("--pool", help="pool-*.json of the run: mark coverage of these symbols (needs --covered-ms)")
    ap.add_argument("--covered-ms", type=int, default=0, help="from when every shard had a live session (coverage.py first_connected max + 3 s)")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    pool = {x.upper() for x in json.load(open(a.pool))["pool"]} if a.pool else None
    runs, cells = {}, {}
    for spec in a.run:
        label, root = spec.split("=", 1)
        d, any_, timely, trades = disk(root, a.t0, a.t1, pool, a.covered_ms or a.t0)
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
