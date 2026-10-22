#!/usr/bin/env bash
# TK-359 §2.1: смерти стен (eaten/pulled) читаются готовым кодом — `lob touches --levels-out` (outcome, death_ms, price_tick,
# side, size_max) + ряд `lob seconds` (mid2 конца секунды). 0 строк Rust. Проба на 1 монето-сутках (SYM, D).
# Запуск: python3 /data/sched/alsched.py submit --cls prod --kind gate --name tk359-deaths --max-runtime 15m --cores 1 -- bash /data/tk0359/tk359-deaths.sh
# Итог: /data/tk0359/deaths/summary.txt, метка deaths.done.
set -uo pipefail
W=/data/tk0359; SYM=${SYM:-ATOMUSDT}; D=${D:-2026-02-10}; O=$W/deaths; BIN=$W/new/alpha-new
mkdir -p "$O"; : > "$O/summary.txt"; rm -f "$O/deaths.done"
R=$W/roots/$SYM
"$BIN" lob touches --root "$R" --symbol "$SYM" --h3-mode notional --h3-usd 10000 --approach-bps 20 --approach-min-age-secs 900 \
  --repeat-window-ms 3600000 --out "$O/touches-$SYM.csv" --levels-out "$O/levels-$SYM.csv" > "$O/touches.log" 2>&1; rc=$?
echo "touches rc=$rc" >> "$O/summary.txt"
python3 - "$O/levels-$SYM.csv" "$W/out/$SYM/b/$SYM-$D.csv" "$D" >> "$O/summary.txt" 2>&1 <<'PY'
import csv, sys, collections
lv, sec, day = sys.argv[1:4]
rows = list(csv.DictReader(l for l in open(lv, encoding="utf-8") if not l.startswith("#")))
mid = {}
for r in csv.DictReader(open(sec, encoding="utf-8")):
    mid[int(r["t_s"])] = (r["mid2"], r["mid2_100"])
t0 = min(mid)  # t_s сек от начала суток; смерть → секунда death_ms//1000 (по модулю суток)
oc = collections.Counter(r["outcome"] for r in rows)
print("levels rows", len(rows), "outcomes", dict(oc))
n = ok = 0
for r in rows:
    if r["outcome"] not in ("eaten", "pulled"):
        continue
    s = int(r["death_ms"]) // 1000
    n += 1
    # база markout_bps — mid2 конца секунды смерти (§3), горизонты — mid2 строк s+1, s+10, s+60, s+600, s+3600
    base = mid.get(s)
    if base and base[0] != "" and all(mid.get(s + h, ("",))[0] != "" for h in (1, 10, 60)):
        ok += 1
print("eaten+pulled", n, "с mid2 базы и горизонтов 1/10/60 с", ok)
print("columns", ",".join(rows[0].keys()) if rows else "-")
print("t_s range", t0, max(mid), "death_ms range", min(int(r["death_ms"]) for r in rows), max(int(r["death_ms"]) for r in rows))
PY
echo "py rc=$?" >> "$O/summary.txt"
touch "$O/deaths.done"
