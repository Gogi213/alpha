#!/usr/bin/env bash
# TK-345 прогон валидности: сутки × символы × (формы вход×стоп×тейк×дедлайн×выход×early) × наборы фильтров; --signal touch, кэш D20.
# Калибровка: Декартово произведение на малом наборе символов; прогон дважды (A, B) для проверки детерминизма.
#   tk345-validity-day.sh <сутки> <выход-каталог> [символы через запятую]    env: BIN GT
set -uo pipefail
d=$1; O=$2; SYMS=${3:-0GUSDT,1000BONKUSDT,1000PEPEUSDT,AAVEUSDT}
BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk0289c-pgo}; GT=${GT:-3}
mon=$(case ${d:5:2} in 01) echo jan;; 02) echo feb;; 03) echo mar;; 04) echo apr;; 05) echo may;; 06) echo jun;; 07) echo jul;; 08) echo aug;; 09) echo sep;; esac)
H=/data/tk046/$mon/home; E=$H/alpha/epochs/e-$mon
export HOME=$H
sargs=(); for s in ${SYMS//,/ }; do sargs+=(--symbol "$s"); done
mkdir -p "$O"; rm -f "$O/done" "$O/fail"
cd "$E" || exit 3
for run in A B; do
  rm -rf "$O/$run"; mkdir -p "$O/$run"
  t1=$(date +%s)
  nice -n 5 "$BIN" lob bounce-grid --root "study/root-$d" --day "$d" "${sargs[@]}" --touches-from study/approaches/D20 --regime-from study/regime \
    --queue-model prob:3 --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
    --order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 --band-exit-bps 20 --busy-skip off --threads "$GT" --hold-step skip --exit-group on --events wide \
    --entry-form single@fr --entry-form market \
    --stop-form before --stop-form pct0.5 --stop-form pct2 --take-form 1to1 --take-form tr1x1 --deadline-secs 1800 --deadline-secs 14400 \
    --exit-form none --exit-form eat50 --exit-form gone50 --early-exit-secs 2 --early-exit-secs off \
    --set base:side=bid --set age1800:side=bid,age=1800 --set age7200:side=bid,age=7200 --set usd30k:side=bid,usd_min=30000 \
    --set flow100:side=bid,flow=100 --set fr:side=bid,frontrun --set btcup:side=bid,btc4h_min=0 --set btcdn:side=bid,btc4h_max=0 \
    --set ret1hup:side=bid,ret1h_min=0 --set ret1hdn:side=bid,ret1h_max=0 --set age1800usd30k:side=bid,age=1800,usd_min=30000 --set ask:side=ask \
    --out-dir "$O/$run/out" > "$O/$run/grid.log" 2>&1
  rc=$?; echo "$run rc=$rc s=$(( $(date +%s)-t1 ))" >> "$O/units.txt"
  [ $rc -eq 0 ] || { echo "$run rc=$rc" > "$O/fail"; tail -5 "$O/$run/grid.log" >> "$O/fail"; exit $rc; }
done
python3 -I "${CHECK:-/data/tk0345/tk345-validity-check.py}" "$O" > "$O/check.txt" 2>&1
echo "check rc=$?" >> "$O/units.txt"
touch "$O/done"
