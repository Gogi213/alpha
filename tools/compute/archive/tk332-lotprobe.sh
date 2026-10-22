#!/bin/bash
# TK-332, П-22: проба округления лотов спот→фьюч (touches-project, convert_lots) на подходах СПОТА.
# По каждой монете: сколько ненулевых значений колонок-лотов спота стали 0 лотов фьюча. Выход: <OUT>/<SYM>/report.txt
# Запуск на calc под alsched: tk332-lotprobe.sh [BIN] [OUT] [DAY] [SYM…]  (умолчание: r2 TK-328, /data/tk0328/lotprobe, 2026-05-05, SUI LTC BNB AAVE)
set -euo pipefail
BIN=${1:-/data/tk0328/b4/alpha-new} OUT=${2:-/data/tk0328/lotprobe} DAY=${3:-2026-05-05}; shift 3 2>/dev/null || shift $# 2>/dev/null || true
SYMS=${*:-SUI LTC BNB AAVE}
SS=${SS:-/data/tk0319/e-may/study/root-$DAY} FS=${FS:-/data/tk046/may/home/alpha/epochs/e-may/root} JRN=${JRN:-/data/tk0319/jrn}
for s in $SYMS; do
  sym=${s}USDT d=$OUT/$s; rm -rf "$d"; mkdir -p "$d"
  "$BIN" lob touches-project --in "$JRN/$DAY/wt/sub/D20/$DAY/approaches-$sym.csv" --out "$d/proj.csv" \
    --spot "$SS/$sym-$DAY.binlog" --fut "$FS/$sym-$DAY.binlog" > "$d/project-report.txt"
  python3 - "$JRN/$DAY/wt/sub/D20/$DAY/approaches-$sym.csv" "$d/proj.csv" "$d/project-report.txt" > "$d/report.txt" <<'PY'
import csv, sys
a, b, rep = sys.argv[1:4]
ra, rb = list(csv.DictReader(open(a))), list(csv.DictReader(open(b)))
print("проекция:", open(rep).read().replace("\n", " "))
# проекция отбрасывает строки без mid / вне i64 — сопоставляем по (arm_ms, side, price_tick до проекции нельзя) → по порядку: проекция сохраняет порядок
it = iter(ra)
pairs = []
for r in rb:
    for x in it:
        if x["arm_ms"] == r["arm_ms"] and x["side"] == r["side"]:
            pairs.append((x, r)); break
cols = [c for c in ra[0] if "lots" in c or c in ("size_at_arm", "size_max_at_arm", "trade_size_p50_15m", "trade_size_p90_15m")]
print(f"строк спота {len(ra)}, проекция {len(rb)}, сопоставлено {len(pairs)}")
print("колонка\tненулевых спота\tстали 0 фьюча\tдоля")
for c in cols:
    nz = [(x, r) for x, r in pairs if x[c] not in ("", "0")]
    z = sum(1 for x, r in nz if r[c] in ("", "0"))
    print(f"{c}\t{len(nz)}\t{z}\t{(z / len(nz) if nz else 0):.3f}")
PY
done
