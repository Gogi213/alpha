#!/usr/bin/env bash
# TK-611 ворота f12, большой эталон: `/data/tk594/ep_u0.parquet` (46 046 эпизодов, T0 2026-09-01, сетка 74 суток — `SIGFEAT_DAYS` TK-594, кэш `/data/p42-t566/cache`) против `alpha-feat f12`. srv.
#   BIN=/data/p42-tk611/bin/alpha-feat-f12s OUT=/data/p42-tk611/u0 PY=/data/p42-tk611/py [PROCS=4] [BIN_ARGS=…] bash rustfeat_f12_u0.sh
# sigsx — `/data/tk592/out/eng/b0p0/<side>-sigsx.csv`, строки отфильтрованы по ключам `(sym, level, sig_ms)` эталона (строки целиком, без пересчёта форматов);
# сравнение по ключу `sym,level,sig_ms` и стороне. Решающее — все столбцы, кроме `ep_no`, `n_sig_ep`, `last_sig_ms`: они различаются по построению (`ep_u0` — маршрут `Worker`,
# сигналы уровня слиты в эпизоды; порт — `CandWorker`, строка sigsx = эпизод; см. `rustfeat_f12_u0_classes.py`, он же проверяет, что на эпизодах из одного сигнала n_sig_ep/last_sig_ms совпадают).
# `emp_coin` зависит от длины сетки: `--days 74` (TK-594), не 80.
set -uo pipefail
BIN=${BIN:?BIN}; OUT=${OUT:-/data/p42-tk611/u0}; PY=${PY:-/data/p42-tk611/py}; PROCS=${PROCS:-4}; BIN_ARGS=${BIN_ARGS:-}
REF=${REF:-/data/tk594/ep_u0.parquet}; SX=${SX:-/data/tk592/out/eng/b0p0}; CACHE=${CACHE:-/data/p42-t566/cache}; DAYS=${DAYS:-74}; TR=${TR:-/data/aggtrades/binance}
mkdir -p "$OUT"; rc=0
MD5=$(md5sum "$BIN" | cut -d" " -f1); echo "F12-U0 bin=$BIN md5=$MD5 args=[$BIN_ARGS]"; echo "$MD5" > "$OUT/md5"
python3 - "$REF" "$SX" "$OUT" <<'EOF'
import csv, sys
import pandas as pd
ref, sx, out = sys.argv[1:4]
d = pd.read_parquet(ref, columns=["side", "sym", "level", "sig_ms"])
for side in ("long", "short"):
    keys = set(zip(d[d.side == side].sym, d[d.side == side].level.astype(int), d[d.side == side].sig_ms.astype("int64")))
    n = 0
    with open(f"{sx}/{side}-sigsx.csv", encoding="utf-8", newline="") as f, open(f"{out}/{side}-sigsx.csv", "w", encoding="utf-8", newline="") as g:
        rd, w = csv.reader(f), csv.writer(g, lineterminator="\n")
        w.writerow(next(rd))
        for r in rd:
            if (r[0], int(r[1]), int(r[2])) in keys:
                w.writerow(r)
                n += 1
    print(side, "keys", len(keys), "sigsx rows", n, flush=True)
    assert n == len(keys), f"{side}: ключей {len(keys)} — строк sigsx {n}"
EOF
[ $? -eq 0 ] || exit 2
for side in long short; do
  s0=$(date +%s.%N)
  nice -n 19 "$BIN" f12 --sigsx "$OUT/$side-sigsx.csv" --side "$side" --out "$OUT/$side.csv" --cache "$CACHE" --trades "$TR" --base-date 2026-09-01 --days "$DAYS" --threads "$PROCS" $BIN_ARGS > "$OUT/$side.log" 2>&1 || { echo "$side: rust rc!=0"; rc=1; continue; }
  s1=$(date +%s.%N)
  python3 "$PY/rustfeat_f12_gate.py" compare "$REF" "$OUT/$side.csv" --key sym,level,sig_ms --side "$side" --out "$OUT/$side.full.json" > "$OUT/$side.full.gate" 2>&1   # все столбцы, для справки
  if python3 "$PY/rustfeat_f12_gate.py" compare "$REF" "$OUT/$side.csv" --key sym,level,sig_ms --side "$side" --exclude ep_no,n_sig_ep,last_sig_ms --out "$OUT/$side.json" > "$OUT/$side.gate" 2>&1; then r=PASS; else rc=1; r=FAIL; fi
  python3 "$PY/rustfeat_f12_u0_classes.py" "$REF" "$OUT/$side.csv" "$side" > "$OUT/$side.classes" 2>&1 || { rc=1; r=FAIL; }
  echo "$side $r md5=${MD5:0:8} rust_s=$(awk "BEGIN{printf \"%.1f\", $s1-$s0}") $(tail -1 "$OUT/$side.gate") | $(tail -1 "$OUT/$side.log" | cut -c1-200)"
  echo "  full (all columns, informational): $(tail -1 "$OUT/$side.full.gate")"; cat "$OUT/$side.classes"
done
echo "F12-U0 md5=$MD5 rc=$rc"; echo $rc > "$OUT/rc"
