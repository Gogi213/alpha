#!/usr/bin/env bash
# TK-479: convert + bench ядра statarb на calc (панели .sap, пропускная способность по видам).
# Окна/пороги ниже — аргументы замера скорости (CLI требует их все), не назначение: бэктест параметров — отдельный тикет.
#   statarb-bench.sh <бинарь alpha> <N символов> <потоки>
set -eu
BIN="${1:?бинарь}"; N="${2:-50}"; TH="${3:-4}"
S=/srv/alpha-sda/candles-1m; W=/data/tk0479
mkdir -p "$W"
# N самых длинных по размеру архива на Bybit; на Binance — те же имена (для cross)
ls -S "$S/bybit-linear" | grep '\.csv\.gz$' | head -n "$N" > "$W/names.txt"
for ex in bybit-linear binance-um; do
  d="$W/src-$ex"; rm -rf "$d" "$W/panels-$ex"; mkdir -p "$d"
  while read -r f; do [ -e "$S/$ex/$f" ] && ln -s "$S/$ex/$f" "$d/$f"; done < "$W/names.txt"
  echo "== convert $ex ($(ls "$d" | wc -l) файлов, потоков $TH)"
  "$BIN" statarb convert --src "$d" --out "$W/panels-$ex" --threads "$TH"
done
NP=$((N * (N - 1) / 2)); NEVER=4000000000   # refresh=NEVER — окно катится без пересчёта
b() { echo "-- $*"; "$BIN" statarb bench --panels "$W/panels-bybit-linear" --symbols "$N" --min-span 1440 --min-obs 240 --entry-z 2 --exit-z 0.5 --max-hold 1440 "$@"; }
for kind in pair cross basket; do
  X=(); [ "$kind" = cross ] && X=(--panels-b "$W/panels-binance-um"); [ "$kind" = basket ] && X=(--basket-k 10)
  J=$NP; [ "$kind" != pair ] && J=$N
  b --kind "$kind" "${X[@]}" --max-jobs 8 --threads "$TH" --window 240 --refresh 240 --engine reference
  b --kind "$kind" "${X[@]}" --max-jobs "$J" --threads "$TH" --window 240 --refresh $NEVER --engine kernel
  b --kind "$kind" "${X[@]}" --max-jobs "$J" --threads "$TH" --window 240 --refresh 240 --engine kernel
  b --kind "$kind" "${X[@]}" --max-jobs "$J" --threads "$TH" --window 1440 --refresh 1440 --engine kernel
  b --kind "$kind" "${X[@]}" --max-jobs "$J" --threads 1 --window 240 --refresh 240 --engine kernel
done
