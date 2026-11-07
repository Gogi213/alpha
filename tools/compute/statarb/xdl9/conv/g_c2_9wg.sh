#!/bin/bash
# TK-493, П-38 r3 (GW 07.11): гейты G-C2-9w (основной набор, без Gate) и G-C2-9wg (блок «с Gate») ОДНИМ запуском раннера окна conv_window.py в режиме test-boundary на ФОРМИРОВАНИИ
# (граница D, «окно» D..2478; сутки >= 2479 не читаются, токен не нужен и не принимается) против `conv_run grid` на тех же сутках и числах: массивы tab/act/hist/minnet, pids, pairmeta,
# counters, trades.csv - до бита; ряд y_d и интервалы - равны. Основной набор сверяется с `conv_run grid --no-gate` (кэш набора без Gate), блок Gate - с `conv_run grid --with-gate` (кэш набора
# с Gate). Числа вариантов (комбинации, classpar, q5) берутся из spec (тот же spec, что у окна). Запуск на Deck:
#   systemd-run --user --unit=c2w-g9wg --collect -p MemoryMax=12G --nice=10 bash -c 'bash g_c2_9wg.sh > LOG 2>&1'
# Переменные: D (2472), TH (5), C2 (~/xdl9/c2), G2 (~/xdl9/c2g), SPEC (spec окна), OUT (выходы раннера), BASE (базовые сетки conv_run), NOCACHE (кэш рядов набора без Gate), GCACHE (с Gate),
# TURN_MAIN ($C2/turn: боковые панели оборота 8 бирж), TURN_GATE ($G2/turn: те же + ноги Gate - у базы с Gate ОБЯЗАТЕЛЬНО свои панели оборота, иначе ноги Gate без оборота и сверка расходится),
# SAMPLE (1 - писать замер ресурсов в $OUT.res.csv; 0 - не писать), SKIPBASE (1 - не пересчитывать базовые сетки, взять готовые в $BASE), SKIPRUN (1 - не запускать раннер, сверять готовый $OUT),
# SKIPCMP (1 - без сверки: только замер раннера). Выходы - под $C2/gw_test/.
set -u
D=${D:-2472}
TH=${TH:-5}
C2=${C2:-$HOME/xdl9/c2}
G2=${G2:-$HOME/xdl9/c2g}
HERE=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
ROOT=$(cd "$HERE/../../../../.." && pwd)
PY=${PY:-$HOME/xdl9/venv/bin/python}
SPEC=${SPEC:-$ROOT/docs/research/P-38-calib-gate-2026-11-07/window_spec_prod.json}
OUT=${OUT:-$C2/gw_test/D$D}
BASE=${BASE:-$C2/gw_test/base_D$D}
NOCACHE=${NOCACHE:-$G2/ng/out/calib/cache}
GCACHE=${GCACHE:-$G2/out/calib/cache}
TURN_MAIN=${TURN_MAIN:-$C2/turn}
TURN_GATE=${TURN_GATE:-$G2/turn}
export C2_HOME=$C2
cd "$HERE" || exit 2
echo "START $(date -u +%FT%TZ) D=$D TH=$TH CODE=$HERE SPEC=$SPEC OUT=$OUT"
df -h /home | tail -1
free -m | head -2
mkdir -p "$BASE" "$(dirname "$OUT")"
SPID=""
if [ "${SAMPLE:-1}" = "1" ]; then
  bash "$HERE/res_sample.sh" "$OUT.res.csv" 5 'conv_window.py run' &
  SPID=$!
fi

# базовая сетка conv_run: $1 набор (main|gate), $2 вариант (V1|V2), $3 флаг Gate (--no-gate|--with-gate), $4 кэш рядов набора, $5 боковые панели оборота набора
base_grid() {
  eval "$($PY "$HERE/spec_variant.py" "$SPEC" "$1" "$2" "$BASE/$1_$2")"
  mkdir -p "$BASE/$1_$2/calib"
  ln -sfn "$4" "$BASE/$1_$2/calib/cache"
  QARG=""
  [ -n "$Q5" ] && QARG="--q5 $Q5"
  echo "--- базовая сетка conv_run ($1 $2 $3): combo=$(cat "$BASE/$1_$2/combos.json")"
  nice -n 10 $PY conv_run.py grid --out "$BASE/$1_$2" --mode calib --last-day 2478 --first-sig-day "$D" --L 1440 --tag b --classes "$CLS" --combos "$BASE/$1_$2/combos.json" \
      --classpar "$CP" $QARG $3 --trades-out --threads "$TH" --turn-dir "$5" 2>&1 | tail -3
}
if [ "${SKIPBASE:-0}" != "1" ]; then
  for V in V1 V2; do
    base_grid main $V --no-gate "$NOCACHE" "$TURN_MAIN"
    base_grid gate $V --with-gate "$GCACHE" "$TURN_GATE"
  done
fi
if [ "${SKIPRUN:-0}" != "1" ]; then
  echo "--- раннер окна: test-boundary $D (основной набор + блок Gate)"
  nice -n 10 $PY conv_window.py run --spec "$SPEC" --test-boundary "$D" --out "$OUT" --threads "$TH" 2>&1 | tail -40
fi
R1=0
R2=0
if [ "${SKIPCMP:-0}" != "1" ]; then
  echo "--- сверка G-C2-9w (основной набор, без Gate)"
  $PY conv_window.py gate9w --run "$OUT" --base V1="$BASE/main_V1/calib/grid_b" --base V2="$BASE/main_V2/calib/grid_b"
  R1=$?
  echo "--- сверка G-C2-9wg (блок с Gate)"
  $PY conv_window.py gate9w --run "$OUT/gate" --base V1="$BASE/gate_V1/calib/grid_b" --base V2="$BASE/gate_V2/calib/grid_b"
  R2=$?
fi
if [ -n "$SPID" ]; then
  kill "$SPID" 2> /dev/null
  bash "$HERE/res_summary.sh" "$OUT.res.csv"
fi
df -h /home | tail -1
echo "EXIT g9w=$R1 g9wg=$R2 $(date -u +%FT%TZ)"
[ $R1 -eq 0 ] && [ $R2 -eq 0 ]
