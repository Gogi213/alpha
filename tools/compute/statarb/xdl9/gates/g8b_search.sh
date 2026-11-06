#!/usr/bin/env bash
# TK-493 F6, G8b (шаг 1): поиск заданий, где позиция ОСТАЛАСЬ ОТКРЫТОЙ из-за снятия контракта. Кандидаты — контракты, снятые в окне
# ФОРМИРОВАНИЯ (сутки 2465..2478): Binance SETTLING (05.10), Bybit Closed (10.10), Aster SETTLING (02.10). Нога Y — снятый контракт,
# нога X — крупный партнёр той же биржи. Клетки с мягкими порогами (проверка механики, не выбор исследования).
set -u
BIN=$HOME/xdl9/bin/statarb-xdl9-02c6a0df
P=$HOME/xdl9/panels; F=$HOME/xdl9/funding
G=$HOME/xdl9/gates/g8b/search; FROM=2465; TO=2479
mkdir -p "$G"; rm -rf "${G:?}"/*
cat > "$G/cells.csv" <<'EOF'
cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh
s0,240,120,,1.0,0.0,inf,0,0
s1,240,120,,1.5,0.5,inf,0,0
s2,120,60,,1.0,0.0,inf,0,0
s3,120,60,,2.0,-0.5,inf,0,0
s4,240,120,,1.0,-1.0,inf,0,0
EOF
search() {  # биржа "кандидаты"
  local ex=$1; shift
  local D=$G/$ex; mkdir -p "$D"
  { echo "y,x,from_day,to_day"; for y in "$@"; do for x in BTCUSDT ETHUSDT SOLUSDT XRPUSDT DOGEUSDT; do echo "$y,$x,$FROM,$TO"; done; done; } > "$D/jobs.csv"
  local n=$(( $(wc -l < "$D/jobs.csv") - 1 ))
  "$BIN" statarb run --kind pair --cells "$G/cells.csv" --jobs "$D/jobs.csv" --panels "$P/$ex" --funding-y "$F/$ex" --min-span 100 --threads 2 \
    --daily-out "$D/daily.csv" --trades-out "$D/trades.csv" --trade-jobs "$(seq -s, 0 $((n-1)))" --mtm-open --cells-out "$D/cells.csv" --open-out "$D/open.csv" > "$D/run.log" 2>&1 || { echo "ОШИБКА $ex"; tail -3 "$D/run.log"; }
  echo "== $ex: $(grep -o 'заданий [0-9]* (посчитано [0-9]*, нет панели [0-9]*, нет спана [0-9]*)' "$D/run.log")"
  cut -d, -f1,6,8-10,13-15 "$D/cells.csv"
  echo "-- открытые позиции на конце окна (cell,job,y,x,dir,sig_in,fill_in,stale_min,net):"
  awk -F, 'NR>1{print $1","$2","$3","$4","$5","$6","$7","$13","$12}' "$D/open.csv" | head -30
}
search binance 1000000BOBUSDT PROMPTUSDT PUMPBTCUSDT
search bybit STGUSDT
search aster PUMPBTCUSDT
