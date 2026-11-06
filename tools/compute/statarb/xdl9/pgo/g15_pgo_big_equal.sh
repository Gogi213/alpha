#!/usr/bin/env bash
# TK-493 pgo, G15: «байт в байт» на крупных наборах (сверх G6/G11): g10 — 120 символов Binance, 7 140 пар, кросс 6 200 заданий, 360 корзин, фандинг;
# выходы (суточный csv, сделки выбранных заданий, --ext-out, --job-day-out, --cells-out) не-PGO эталона и проверяемых бинарей сравниваются sha256.
# Панели — ФОРМИРОВАНИЕ (prep_pform.py). Запуск на Steam Deck.
#   g15_pgo_big_equal.sh <метка:бинарь> [<метка:бинарь> ...]   первый — эталон (не-PGO); код 0 — все выходы равны эталону
set -u
[ $# -ge 2 ] || { echo "нужно: эталон и хотя бы один проверяемый бинарь (метка:путь)"; exit 2; }
TH=${TH:-4}
PF=$HOME/xdl9/pgo/pform; G=$HOME/xdl9/gates/g10; T=${G15_DIR:-$HOME/xdl9/pgo/g15}; FF=$HOME/xdl9/pgo/pfund
rm -rf "${T:?}"; mkdir -p "$T"
NB=$PF/bin120
mapfile -t NM < <(ls "$NB" | sed 's/\.sap$//')
{ echo y,x,from_day,to_day; for i in "${!NM[@]}"; do for d in 1 7 13; do echo "${NM[i]},${NM[(i+d)%${#NM[@]}]}+${NM[(i+d+1)%${#NM[@]}]}+${NM[(i+d+2)%${#NM[@]}]},2465,2479"; done; done; } > "$T/jobs_basket.csv"
T0=0,1,2,3,4,5,6,7,8,9
# w5: флаги --no-entry-before-from и --job-ext-out (D5(ii), L1-03) на крупном наборе: те же задания с from_day 2472 (перед границей есть разогрев),
# клетки z с min_obs = window (как в F8) и правило funding (D2-подобное, ставки из pfund)
sed -E 's/,2465,2479$/,2472,2479/' "$G/jobs_pair.csv" > "$T/jobs2472_pair.csv"
cat > "$T/cellsW.csv" <<'EOF2'
cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh
w0,720,720,,2.0,0.5,inf,720,0
w1,720,720,,1.5,0.3,4.0,0,0
w2,1440,1440,,2.5,0.5,inf,1440,97
w3,720,720,1.0,2.0,0.5,3.5,480,0
w4,1440,1440,,2.0,0.5,3.0,720,200
EOF2
cat > "$T/cellsF.csv" <<'EOF2'
cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh
f0,60,60,1.0,2.0,0.5,inf,240,0
f1,60,60,1.0,2.0,0.5,inf,720,0
EOF2
fail=0
runs() {  # метка бинаря, каталог выхода
  local bin=$1 D=$2; mkdir -p "$D"
  b() { local name=$1; shift; "$bin" statarb run "$@" > "$D/$name.log" 2>&1 < /dev/null || echo "ОШИБКА $name"; }
  b pair        --kind pair   --jobs "$G/jobs_pair.csv" --panels "$NB" --min-span 1000 --threads "$TH" --trade-jobs $T0 --cells "$G/cells5.csv" --daily-out "$D/pair.daily.csv" --trades-out "$D/pair.trades.csv"
  b pair_rearm  --kind pair   --jobs "$G/jobs_pair.csv" --panels "$NB" --min-span 1000 --threads "$TH" --trade-jobs $T0 --trades-ext --rearm --cells "$G/cells5.csv" --daily-out "$D/pr.daily.csv" --trades-out "$D/pr.trades.csv" --ext-out "$D/pr.ext.csv" --job-day-out "$D/pr.jd.csv"
  b pair_mtm    --kind pair   --jobs "$G/jobs_pair.csv" --panels "$NB" --min-span 1000 --threads "$TH" --trade-jobs $T0 --cells "$G/cells_three.csv" --daily-out "$D/pm.daily.csv" --mtm-open --cells-out "$D/pm.cells.csv" --open-out "$D/pm.open.csv" --trades-out "$D/pm.trades.csv"
  b pair_one    --kind pair   --jobs "$G/jobs_pair.csv" --panels "$NB" --min-span 1000 --threads "$TH" --cells "$G/cells_one.csv" --daily-out "$D/po.daily.csv"
  b pair_delay  --kind pair   --jobs "$G/jobs_pair.csv" --panels "$NB" --min-span 1000 --threads "$TH" --delay-x 2 --trade-jobs $T0 --cells "$G/cells_two.csv" --daily-out "$D/pd.daily.csv" --trades-out "$D/pd.trades.csv"
  b pair_fund   --kind pair   --jobs "$G/jobs_pair.csv" --panels "$NB" --funding-y "$FF/binance" --min-span 1000 --threads "$TH" --cells "$G/cells5.csv" --daily-out "$D/pf.daily.csv"
  b cross       --kind cross  --jobs "$G/jobs_cross100.csv" --panels "$NB" --panels-b "$PF/byb120" --min-span 1000 --threads "$TH" --trade-jobs $T0 --trades-ext --rearm --cells "$G/cells5.csv" --daily-out "$D/cr.daily.csv" --trades-out "$D/cr.trades.csv" --ext-out "$D/cr.ext.csv" --job-day-out "$D/cr.jd.csv"
  b cross_fund  --kind funding-pp --jobs "$G/jobs_cross.csv" --panels "$NB" --panels-b "$PF/byb120" --funding-y "$FF/binance" --funding-x "$FF/bybit" --min-span 1000 --threads "$TH" --trade-jobs 0,1,2 --cells "$G/cells5.csv" --daily-out "$D/cf.daily.csv" --trades-out "$D/cf.trades.csv"
  b d3          --kind funding-pp --jobs "$G/jobs_cross.csv" --panels "$NB" --panels-b "$PF/byb120" --funding-y "$FF/binance" --funding-x "$FF/bybit" --min-span 1000 --threads "$TH" --trade-jobs 0,1,2 --cell d3 --window 720 --min-obs 360 --entry-z 2.0 --exit-z 0.5 --stop-z inf --max-hold 720 --refresh 0 --rule funding --funding-k 3 --hold-payments 8 --daily-out "$D/d3.daily.csv" --trades-out "$D/d3.trades.csv"
  b basket      --kind basket --jobs "$T/jobs_basket.csv" --panels "$NB" --min-span 1000 --threads "$TH" --trade-jobs $T0 --trades-ext --rearm --cells "$G/cells5.csv" --daily-out "$D/bk.daily.csv" --trades-out "$D/bk.trades.csv" --ext-out "$D/bk.ext.csv" --job-day-out "$D/bk.jd.csv"
  b pair_w5     --kind pair   --jobs "$T/jobs2472_pair.csv" --panels "$NB" --min-span 1000 --threads "$TH" --trade-jobs $T0 --trades-ext --rearm --no-entry-before-from --mtm-open --cells "$T/cellsW.csv" --daily-out "$D/w5.daily.csv" --trades-out "$D/w5.trades.csv" --ext-out "$D/w5.ext.csv" --job-day-out "$D/w5.jd.csv" --cells-out "$D/w5.cells.csv" --job-ext-out "$D/w5.jext.csv" --open-out "$D/w5.open.csv"
  b pair_w5_d2  --kind pair   --jobs "$T/jobs2472_pair.csv" --panels "$NB" --funding-y "$FF/binance" --rule funding --funding-k 3 --hold-payments 40 --min-span 1000 --threads "$TH" --trade-jobs $T0 --no-entry-before-from --mtm-open --cells "$T/cellsF.csv" --daily-out "$D/w5d.daily.csv" --trades-out "$D/w5d.trades.csv" --cells-out "$D/w5d.cells.csv" --job-ext-out "$D/w5d.jext.csv" --open-out "$D/w5d.open.csv"
  ( cd "$D" && sha256sum *.csv | sed 's/  / /' ) > "$D/sha256.txt"
}
ref="";
for spec in "$@"; do
  tag=${spec%%:*}; bin=${spec#*:}
  runs "$bin" "$T/$tag"
  n=$(wc -l < "$T/$tag/sha256.txt"); rows=$(cat "$T/$tag"/*.trades.csv | wc -l)
  if [ -z "$ref" ]; then ref=$tag; echo "эталон $tag: $n csv, строк сделок всего $rows, бинарь $(sha256sum "$bin" | cut -c1-16)"
  else
    if cmp -s "$T/$ref/sha256.txt" "$T/$tag/sha256.txt"; then echo "РАВНО  $tag = $ref: все $n csv побайтно (строк сделок $rows)"
    else echo "РАЗНО  $tag != $ref"; diff "$T/$ref/sha256.txt" "$T/$tag/sha256.txt" | head -10; fail=$((fail+1)); fi
  fi
done
cp "$T/$ref/sha256.txt" "$T/sha256-ref.txt"
grep -h ОШИБКА "$T"/*/*.log 2>/dev/null | head -3
echo "ИТОГ G15: расхождений $fail"
exit $fail
