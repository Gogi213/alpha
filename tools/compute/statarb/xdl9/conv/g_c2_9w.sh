#!/bin/bash
# TK-493, П-38, гейт G-C2-9w: раннер окна conv_window.py на ФОРМИРОВАНИИ с искусственной границей D (режим test-boundary: токен не нужен, сутки >= 2479 читать нельзя)
# против `conv_run grid --first-sig-day D` (сетка калибровки) на тех же сутках и тех же числах: массивы tab/act/hist/minnet, pids, pairmeta, counters, trades.csv - до бита;
# ряд y_d и интервалы - равны. Запуск на Deck:  systemd-run --user --unit=c2w-g9w --collect -p MemoryMax=8G -p CPUWeight=50 bash -c 'nice -n 10 bash g_c2_9w.sh > LOG 2>&1'
# База считается с --no-gate: универсум калибровки - 8 бирж (режим auto по манифесту Gate мог бы добавить Gate); раннер окна всегда считает без Gate (conv_window.make_world).
# Переменные: D (2472), TH (4 потока), CODE (каталог кода), SPEC (spec окна), OUT (выходы раннера), BASE (базовые сетки conv_run).
set -u
D=${D:-2472}
TH=${TH:-4}
C2=${C2:-$HOME/xdl9/c2}
CODE=${CODE:-$C2/code_w}
PY=${PY:-$HOME/xdl9/venv/bin/python}
SPEC=${SPEC:-$C2/window_spec.json}
OUT=${OUT:-$C2/window_test/D$D}
BASE=${BASE:-$C2/window_test/base_D$D}
export C2_HOME=$C2
cd "$CODE" || exit 2
echo "START $(date -u +%FT%TZ) D=$D TH=$TH CODE=$CODE SPEC=$SPEC"
mkdir -p "$BASE"
for V in V1 V2; do
  eval "$($PY - "$SPEC" "$V" "$BASE" <<'PYEOF'
import json, sys
spec, vid, base = sys.argv[1:4]
v = [x for x in json.load(open(spec, encoding="utf-8"))["variants"] if x["id"] == vid][0]
json.dump([v["combo"]], open("%s/combos_%s.json" % (base, vid), "w"))
print("CP='%s'" % v["classpar"])
print("Q5='%s'" % (v.get("q5") or ""))
print("CLS='%s'" % v["cls"])
PYEOF
)"
  mkdir -p "$BASE/$V/calib"
  ln -sfn "$C2/out/calib/cache" "$BASE/$V/calib/cache"
  QARG=""
  [ -n "$Q5" ] && QARG="--q5 $Q5"
  echo "--- базовая сетка conv_run: $V combo=$(cat $BASE/combos_$V.json)"
  $PY conv_run.py grid --out "$BASE/$V" --mode calib --last-day 2478 --first-sig-day "$D" --L 1440 --tag b --classes "$CLS" --combos "$BASE/combos_$V.json" \
      --classpar "$CP" $QARG --no-gate --trades-out --threads "$TH" --turn-dir "$C2/turn" 2>&1 | tail -3
done
echo "--- раннер окна: test-boundary $D"
$PY conv_window.py run --spec "$SPEC" --test-boundary "$D" --out "$OUT" --threads "$TH" 2>&1 | tail -25
echo "--- сверка G-C2-9w"
$PY conv_window.py gate9w --run "$OUT" --base V1="$BASE/V1/calib/grid_b" --base V2="$BASE/V2/calib/grid_b"
echo "EXIT=$? $(date -u +%FT%TZ)"
