#!/usr/bin/env bash
# TK-479: честное «до/после» на одном ядре — те же 8 пар, reference (наивный O(W)) против kernel. Панели из statarb-bench.sh.
#   statarb-bench1.sh <бинарь alpha>
set -eu
BIN="${1:?бинарь}"; W=/data/tk0479
for kind in pair cross basket; do
  X=(); [ "$kind" = cross ] && X=(--panels-b "$W/panels-binance-um"); [ "$kind" = basket ] && X=(--basket-k 10)
  for e in reference kernel; do
    "$BIN" statarb bench --panels "$W/panels-bybit-linear" --symbols 50 --min-span 1440 --min-obs 240 --entry-z 2 --exit-z 0.5 --max-hold 1440 \
      --kind "$kind" "${X[@]}" --max-jobs 8 --threads 1 --window 240 --refresh 240 --engine "$e"
  done
done
