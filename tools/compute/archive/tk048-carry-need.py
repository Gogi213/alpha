#!/usr/bin/env python3
"""TK-048: сколько окна переноса реально нужно кругам. По rounds.csv/signals.csv выхода (первая строка файла — комментарий
`# lob bounce-grid…`, пропускается): на (монета, сутки)
  need   = максимум exit_ns - полночь D+1 среди кругов с exit_ns >= полночь (нижняя граница, видны только выходы);
  edge   = max(0, last_t0 + horizon - полночь D+1), last_t0 — последний t0_ns суток (rounds + signals), horizon = ttl+deadline+rtt
           (край довеска C' по правилу Судьи 08.10; ttl/deadline — максимумы сетки, поэтому edge — верхняя оценка).
Использование: tk048-carry-need.py <root> <window_s> <horizon_s>. Печатает JSON с распределениями и долями окна."""
import collections
import csv
import datetime
import glob
import json
import sys

root, window_s, horizon_s = sys.argv[1], float(sys.argv[2]), float(sys.argv[3])
need = collections.defaultdict(float)
last_t0 = {}
all_sd = set()
n_rows = 0


def rows(path):
    with open(path, newline="") as f:
        lines = (ln for ln in f if not ln.startswith("#"))
        yield from csv.DictReader(lines)


def midnight_next(day):
    d = datetime.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc)
    return int((d + datetime.timedelta(days=1)).timestamp()) * 10**9


for name in ("rounds.csv", "signals.csv"):
    for p in glob.glob(root + "/**/" + name, recursive=True):
        for row in rows(p):
            try:
                day, sym, t0 = row["day_utc"], row["symbol"], int(row["t0_ns"])
            except (KeyError, ValueError):
                continue
            n_rows += 1
            k = (sym, day)
            all_sd.add(k)
            if t0 > last_t0.get(k, 0):
                last_t0[k] = t0
            if name == "rounds.csv":
                try:
                    ex = int(row["exit_ns"])
                except (KeyError, ValueError):
                    continue
                b = midnight_next(day)
                if ex >= b:
                    need[k] = max(need[k], (ex - b) / 1e9)

edge = {}
for k, t0 in last_t0.items():
    edge[k] = max(0.0, (t0 - midnight_next(k[1])) / 1e9 + horizon_s)
vals = sorted(need.values())
evals = sorted(edge.values())


def q(v, f):
    return v[int(f * (len(v) - 1))] if v else None


n = max(1, len(all_sd))
print(json.dumps({
    "rows": n_rows,
    "symbol_days": len(all_sd),
    "symbol_days_with_carry_need": len(vals),
    "window_s": window_s,
    "need_s_p50": q(vals, .5), "need_s_p90": q(vals, .9), "need_s_p99": q(vals, .99),
    "need_s_max": vals[-1] if vals else None,
    "mean_need_over_window": sum(min(v, window_s) for v in vals) / window_s / n,
    "edge_s_p50": q(evals, .5), "edge_s_p90": q(evals, .9), "edge_s_max": evals[-1] if evals else None,
    "mean_edge_over_window": sum(min(v, window_s) for v in evals) / window_s / n,
    "symbol_days_edge_zero": sum(1 for v in evals if v == 0.0),
}, ensure_ascii=False, indent=1))
