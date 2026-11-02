#!/usr/bin/env bash
# TK-479: таблица пропускной способности ядра для одного вида на уже готовых панелях (после bench.sh).
# Окно/пересчёт — аргументы замера, не назначение. Вид: pair | cross | basket | funding-pp | funding-ps.
#   table.sh <вид> [потоки=15] [бинарь=/data/tk0479/alpha-tk479]
set -eu
KIND="${1:?pair|cross|basket|funding-pp|funding-ps}"; TH="${2:-15}"; BIN="${3:-/data/tk0479/alpha-tk479}"
W=/data/tk0479; F=/srv/alpha-sda/funding; N=50
X=(); J=$N
case "$KIND" in
pair) J=$((N * (N - 1) / 2)) ;;
cross) X=(--panels-b "$W/panels-binance-um") ;;
basket) X=(--basket-k 10) ;;
funding-pp) X=(--panels-b "$W/panels-binance-um" --funding-y "$F/bybit-linear" --funding-x "$F/binance-um") ;;
funding-ps) X=(--panels-b "$W/panels-bybit-spot" --funding-y "$F/bybit-linear") ;;
*) echo "вид?"; exit 2 ;;
esac
"$BIN" statarb bench --panels "$W/panels-bybit-linear" --symbols "$N" --min-span 1440 --min-obs 240 --entry-z 2 --exit-z 0.5 \
  --max-hold 1440 --kind "$KIND" "${X[@]}" --max-jobs "$J" --threads "$TH" --window 240 --refresh 240 --engine kernel
