#!/usr/bin/env bash
# TK-482: повторный замер скорости ядра — чередование прежнего и нового бинарника, N прогонов каждого, медиана и разброс.
#   speed-prev.sh <новый бинарь> <прежний бинарь> <потоки> <прогонов> [pair|cross|funding-pp ...]
# Те же аргументы bench, что в gate-prev.sh; строки `ИТОГ скорость`.
set -u
NEW="${1:?новый бинарь}"; PREV="${2:?прежний бинарь}"; TH="${3:-8}"; NR="${4:-5}"; shift 4
KINDS=("$@"); [ ${#KINDS[@]} -eq 0 ] && KINDS=(pair cross funding-pp)
W=/data/tk0479; F=/srv/alpha-sda/funding; N=50; NP=$((N * (N - 1) / 2))
b() { local bin=$1; shift; "$bin" statarb bench --panels "$W/panels-bybit-linear" --symbols "$N" --min-span 1440 --min-obs 240 \
  --entry-z 2 --exit-z 0.5 --max-hold 1440 --threads "$TH" "$@"; }
med() { sort -g | awk '{a[NR]=$1} END{print a[int((NR+1)/2)], a[1], a[NR]}'; }
for k in "${KINDS[@]}"; do
  case $k in
    pair) args="--kind pair"; jobs=$NP ;;
    cross) args="--kind cross --panels-b $W/panels-binance-um"; jobs=$N ;;
    funding-pp) args="--kind funding-pp --panels-b $W/panels-binance-um --funding-y $F/bybit-linear --funding-x $F/binance-um"; jobs=$N ;;
  esac
  : > /tmp/tk482-sp-prev.$$; : > /tmp/tk482-sp-new.$$
  for i in $(seq "$NR"); do
    for who in PREV NEW; do
      bin=$PREV; f=/tmp/tk482-sp-prev.$$; [ $who = NEW ] && { bin=$NEW; f=/tmp/tk482-sp-new.$$; }
      b "$bin" $args --max-jobs "$jobs" --window 240 --refresh 240 --engine kernel 2>&1 | grep -o '[0-9.]*e[+0-9]* строк/с' | cut -d' ' -f1 >> "$f"
    done
  done
  p=$(med < /tmp/tk482-sp-prev.$$); n=$(med < /tmp/tk482-sp-new.$$)
  echo "ИТОГ скорость $k: медиана/мин/макс прежний [$p] новый [$n] строк/с, новый/прежний(медиана) $(awk -v a="${p%% *}" -v b="${n%% *}" 'BEGIN{printf "%.3f", b/a}')"
  rm -f /tmp/tk482-sp-prev.$$ /tmp/tk482-sp-new.$$
done
