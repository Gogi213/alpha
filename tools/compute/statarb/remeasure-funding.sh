#!/usr/bin/env bash
# TK-489: перемер D2 TK-479 (funding-pp / funding-ps) после E-010 — прежний бинарник (unix-эпоха ставок, пустые ставки)
# и новый (ставки на минутах панелей) чередуются на одних панелях; те же 5 вариантов, что в bench.sh.
# Окна/пороги — аргументы замера (как в bench.sh), не назначение.
#   remeasure-funding.sh <новый бинарь> <прежний бинарь> <потоки> <повторов>
set -u
NEW="${1:?новый бинарь}"; PREV="${2:?прежний бинарь}"; TH="${3:-15}"; NR="${4:-2}"
W=/data/tk0479; F=/srv/alpha-sda/funding; N=50; NEVER=4000000000
b() { local bin=$1; shift; "$bin" statarb bench --panels "$W/panels-bybit-linear" --symbols "$N" --min-span 1440 --min-obs 240 \
  --entry-z 2 --exit-z 0.5 --max-hold 1440 "$@"; }
for rep in $(seq "$NR"); do
  echo "-- повтор $rep NEW cross (digest для сравнения с funding-pp)"
  b "$NEW" --kind cross --panels-b "$W/panels-binance-um" --max-jobs "$N" --threads "$TH" --window 240 --refresh 240 --engine kernel --digest 2>&1
  for kind in funding-pp funding-ps; do
    case $kind in
      funding-pp) X="--panels-b $W/panels-binance-um --funding-y $F/bybit-linear --funding-x $F/binance-um" ;;
      funding-ps) X="--panels-b $W/panels-bybit-spot --funding-y $F/bybit-linear" ;;
    esac
    for v in "8 240 240 reference 15" "$N 240 $NEVER kernel 15" "$N 240 240 kernel 15" "$N 1440 1440 kernel 15" "$N 240 240 kernel 1"; do
      set -- $v
      for who in PREV NEW; do
        bin=$PREV; [ $who = NEW ] && bin=$NEW
        dg=""; [ $who = NEW ] && dg="--digest"
        echo "-- повтор $rep $who $kind jobs=$1 window=$2 refresh=$3 engine=$4 threads=$5"
        b "$bin" --kind "$kind" $X --max-jobs "$1" --threads "$5" --window "$2" --refresh "$3" --engine "$4" $dg 2>&1
      done
    done
  done
done
