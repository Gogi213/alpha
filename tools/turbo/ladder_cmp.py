#!/usr/bin/env python3
"""G-022 turbo: Rust load ladder (`rladder.sh`, json per window) next to the Python ladder of TK-531 (windows.csv, numbers NOT recomputed).

  ladder_cmp.py <TK-531 windows.csv> --src DIR:LABEL:MIN:MAX [--src …] [--out-windows rust-windows.csv]
  --src: a Rust ladder result dir, a label for the "режим" column (e.g. "18 параллельно" / "6 юнитов, повторы подряд"), and the range of steps taken from it.

Prints, per step (kind, group, streams): Python and Rust ranges over the windows for msg/s, KB/s, peak msg/s, drops (disconnects), silence > 5 s, lag p50, lag p99, CPU % of a core.
Verdict rule fixed before the run: at a step Rust is WORSE than Python if the median of the Rust lag p99 over its windows is above the MAX of the Python lag p99 windows,
or Rust has more drops (disconnects) than Python. The first such step per (kind, group), in ascending streams, is where the defect points.
Python full-pool step 522: book = the 12.11 repeat ('all-повтор', the 11.11 windows were empty), agg / mark = 'all'.
"""
import argparse
import csv
import glob
import json
import os
import re
import statistics
from collections import defaultdict


def rng(xs):
    xs = [x for x in xs if x is not None]
    if not xs:
        return "-"
    lo, hi = round(min(xs)), round(max(xs))
    return str(lo) if lo == hi else f"{lo}–{hi}"


def py_group(kind, group, n):
    if group == "top" and n == 522:
        return "all-повтор" if kind == "book" else "all"
    return group


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("py_csv")
    ap.add_argument("--src", action="append", required=True, help="DIR:LABEL:MIN:MAX")
    ap.add_argument("--out-windows")
    a = ap.parse_args()
    py = defaultdict(list)
    for r in csv.DictReader(open(a.py_csv, encoding="utf-8")):
        py[(r["kind"], r["group"], int(r["streams"]))].append(r)
    rs, mode = defaultdict(list), {}
    for spec in a.src:
        path, label, lo, hi = spec.rsplit(":", 3)
        for f in sorted(glob.glob(os.path.join(path, "*.json"))):
            m = re.match(r"(\w+)-(\w+)-n(\d+)-r(\d)", os.path.basename(f))
            if not m or os.path.basename(f) == "symbols.json" or not int(lo) <= int(m.group(3)) <= int(hi):
                continue
            d = json.load(open(f))
            d["_f"] = os.path.basename(f)
            rs[(m.group(1), m.group(2), int(m.group(3)))].append(d)
            mode[(m.group(1), m.group(2), int(m.group(3)))] = label
    if a.out_windows:
        with open(a.out_windows, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["kind", "group", "streams", "file", "rtt_ms", "msgs", "msg_s", "kb_s", "peak_msg_s", "lag_p50_ms", "lag_p90_ms", "lag_p99_ms", "lag_max_ms", "disconnects", "silent_s", "cpu_s", "error"])
            for k in sorted(rs, key=lambda k: (k[0], k[1], k[2])):
                for d in rs[k]:
                    w.writerow([k[0], k[1], k[2], d["_f"], d.get("rtt_ms"), d.get("msgs"), d.get("msg_s"), d.get("kb_s"), d.get("peak_msg_s"), d.get("lag_p50"), d.get("lag_p90"), d.get("lag_p99"), d.get("lag_max"),
                                d.get("disconnects"), d.get("silent_s"), d.get("cpu_s"), d.get("error", "")])
    print("| вид | группа | потоков | режим Rust | Python: сообщ/с | Rust: сообщ/с | Python: КБ/с | Rust: КБ/с | Python: пик | Rust: пик | обрывов Py / Rust | тишина >5 с Py / Rust | лаг p50 Py / Rust, мс | лаг p99 Py (по окнам) | лаг p99 Rust (по окнам) | CPU % ядра Py / Rust | Rust хуже |")
    print("|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|")
    first = {}
    for k in sorted(rs, key=lambda k: (k[0], k[1], k[2])):
        kind, group, n = k
        P = [r for r in py.get((kind, py_group(kind, group, n), n), []) if float(r["msgs"] or 0) > 0]
        R = [d for d in rs[k] if d.get("msgs")]
        rfail = [d for d in rs[k] if not d.get("msgs")]
        pf = lambda col: [float(r[col]) for r in P if r[col] not in ("", None)]  # noqa: E731
        p99p, p99r = pf("lag_p99_ms"), [d["lag_p99"] for d in R if d.get("lag_p99") is not None]
        dp = sum(int(r["disconnects"]) for r in P)
        dr = sum(d.get("disconnects", 0) for d in rs[k])
        worse = []
        if p99p and p99r and statistics.median(p99r) > max(p99p):
            worse.append("p99")
        if dr > dp:
            worse.append("обрывы")
        if worse and (kind, group) not in first:
            first[(kind, group)] = (n, worse)
        cpu_p = [float(r["cpu_s"]) / float(r["dur_s"]) * 100 if "dur_s" in r else float(r["cpu_s"]) / 180 * 100 for r in P]
        cpu_r = [d["cpu_s"] / d["dur"] * 100 for d in R if d.get("cpu_s") is not None]
        note = ("; ".join(worse) if worse else "нет") + (f" (окон без данных у Rust: {len(rfail)})" if rfail else "")
        print(f"| {kind} | {group} | {n} | {mode[k]} | {rng(pf('msg_s'))} | {rng([d['msg_s'] for d in R])} | {rng(pf('kb_s'))} | {rng([d['kb_s'] for d in R])} | {rng(pf('peak_msg_s'))} | {rng([d['peak_msg_s'] for d in R])} | "
              f"{dp} / {dr} | {max(pf('silent_s'), default='-')} / {max((d['silent_s'] for d in R), default='-')} | {rng(pf('lag_p50_ms'))} / {rng([d['lag_p50'] for d in R])} | {rng(p99p)} | {rng(p99r)} | {rng(cpu_p)} / {rng(cpu_r)} | {note} |")
    print()
    for (kind, group), (n, worse) in sorted(first.items()):
        print(f"- первая ступень, где Rust хуже Python: {kind} {group} — {n} потоков ({', '.join(worse)})")
    if not first:
        print("- ни на одной ступени Rust не хуже Python по правилу (медиана p99 Rust выше максимума p99 Python; обрывы Rust > Python)")


if __name__ == "__main__":
    main()
