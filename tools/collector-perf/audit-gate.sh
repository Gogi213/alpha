#!/bin/bash
# Условие накатa final6j в бой (14.11): D (final6j) против боевого бинаря C1 при шуме C1 против C2 — (а) тождество и потери `collect compare` (0/0/0), (б) ЦП на кадр по снимкам audit-cpu.sh (t1, t2), (в) набор событий.
# usage: audit-gate.sh <тег1> <тег2>
cd /data/perf; A=/data/perf/audit; B=/data/perf/alpha-final6j
python3 - $A $1 $2 <<'PY'
import sys
A, t1, t2 = sys.argv[1:]
def rd(t):
    d = {}
    for ln in open(f"{A}/cpu-{t}.txt"):
        k, ts, ns, fr, rec = ln.split(); d[k] = (float(ts), int(ns), int(fr), int(rec))
    return d
a, b = rd(t1), rd(t2)
print(f"{'рука':14s} {'окно с':>7s} {'ядер':>7s} {'кадр/с':>8s} {'мкс/кадр':>9s} {'записей/с':>10s}")
res = {}
for k in a:
    dt = b[k][0] - a[k][0]; cpu = (b[k][1] - a[k][1]) / 1e9; fr = b[k][2] - a[k][2]; rec = b[k][3] - a[k][3]
    res[k] = cpu / max(fr, 1) * 1e6
    print(f"{k:14s} {dt:7.0f} {cpu / dt:7.4f} {fr / dt:8.0f} {res[k]:9.2f} {rec / dt:10.1f}")
print()
for ex in ("binance", "bybit", "okx", "bingx"):
    d, c1, c2 = res[ex + "-D"], res[ex + "-C1"], res[ex + "-C2"]
    print(f"{ex:8s} D−C1 {100 * (d / c1 - 1):+5.1f} %, шум C2−C1 {100 * (c2 / c1 - 1):+5.1f} % (мкс на кадр)")
PY
echo; echo "== события по типам (D / C1 / C2)"
for ex in binance bybit okx bingx; do
  for arm in D C1 C2; do
    echo "$ex-$arm: $(cat $A/$ex-$arm/data/events-*.jsonl | grep -o '"ev":"[a-z_]*"' | sort | uniq -c | awk '{printf "%s=%s ", $2, $1}' | sed 's/"ev"://g; s/"//g')"
  done
done
echo; echo "== тождество и потери (collect compare)"
for ex in binance bybit okx bingx; do
  echo "--- $ex D vs C1"; $B collect compare --a $A/$ex-D/data --b $A/$ex-C1/data 2>&1 | head -4 | cut -c1-260
  echo "--- $ex C1 vs C2 (шум)"; $B collect compare --a $A/$ex-C1/data --b $A/$ex-C2/data 2>&1 | head -4 | cut -c1-260
done
