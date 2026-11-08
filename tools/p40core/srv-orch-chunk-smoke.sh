#!/bin/bash
# TK-500: orch.py --chunk: (1) K=1, явные lot -> 5 таблиц байт в байт с /tmp/old.csv* (после правки симлинков/P40_HOURS); (2) DOGE 17-18.09, K=2 vs 2 прогона K=1: эпизодов в K=2 не меньше, первые 6 ч 18.09 не пустые.
cd /root/p40core || exit 1
rm -rf /tmp/orchc1 /tmp/orchc2 /tmp/orchc3
python3 orch.py pairs_doge.csv /tmp/orchc1 --days 2026-09-17 --jobs 1 2>&1 | tail -2
O=/tmp/orchc1/1/2026-09-17
for f in "" .fills.csv .exits.csv .features.csv .path.csv; do cmp /tmp/old.csv$f $O/1$f && echo "байт в байт: $f"; done
python3 orch.py pairs_doge_auto.csv /tmp/orchc2 --days 2026-09-17,2026-09-18 --jobs 1 2>&1 | tail -2
python3 orch.py pairs_doge_auto.csv /tmp/orchc3 --days 2026-09-17,2026-09-18 --chunk 2 --jobs 1 2>&1 | tail -2
for d in /tmp/orchc2/1/2026-09-17 /tmp/orchc2/1/2026-09-18 /tmp/orchc3/1/2026-09-17+2; do echo "$d: $(grep -c '^5.04' $d/1.exits.csv) эпизодов θ=5.04"; done
python3 - <<'PY'
import csv, collections
c = collections.Counter()
for r in csv.DictReader(open("/tmp/orchc3/1/2026-09-17+2/1.exits.csv")):
    if r["theta"] == "5.04":
        c[(int(r["t0_ms"]) // 3600000) % 48] += 1
print("по часам от 17.09 00:00 UTC (0-47):", sorted(c.items())[:12], "...", len(c), "часов с эпизодами")
PY
cat /tmp/orchc3/specs.txt | head -2; uptime
