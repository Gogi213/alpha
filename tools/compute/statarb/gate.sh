#!/usr/bin/env bash
# TK-479: Gate-панели (convert, правило пропусков) и кросс-биржа с ногой Gate; дрейф скользящих сумм против референса
# на реальных данных (8 заданий). Панели Bybit/Binance — из bench.sh. Окна/пороги — аргументы замера, не назначение.
#   gate.sh <бинарь alpha> <потоки>
set -eu
BIN="${1:?бинарь}"; TH="${2:-8}"
S=/srv/alpha-sda/candles-1m; W=/data/tk0479; N=50; NEVER=4000000000
d="$W/src-gate-futures_usdt"; rm -rf "$d" "$W/panels-gate-futures_usdt"; mkdir -p "$d"
# имя Bybit XUSDT → файл Gate X_USDT; в каталоге ссылка под именем Bybit — задания cross сопоставляются по имени
miss=0
while read -r f; do
  g="${f%USDT.csv.gz}_USDT.csv.gz"
  if [ -e "$S/gate-futures_usdt/$g" ]; then ln -s "$S/gate-futures_usdt/$g" "$d/$f"; else miss=$((miss + 1)); fi
done < "$W/names.txt"
echo "== gate: файлов $(ls "$d" | wc -l), без пары на Gate $miss"
"$BIN" statarb convert --src "$d" --out "$W/panels-gate-futures_usdt" --threads "$TH"
b() { echo "-- $*"; "$BIN" statarb bench --symbols "$N" --min-span 1440 --min-obs 240 --entry-z 2 --exit-z 0.5 --max-hold 1440 --window 240 "$@"; }
for pair in "bybit-linear gate-futures_usdt" "binance-um gate-futures_usdt"; do
  set -- $pair
  for e in reference kernel; do
    J=$N; [ "$e" = reference ] && J=8
    b --panels "$W/panels-$1" --panels-b "$W/panels-$2" --kind cross --max-jobs "$J" --threads "$TH" --refresh 240 --engine "$e"
  done
done
# дрейф: (б) без пересчёта и (в) пересчёт раз в окно против референса, те же 8 заданий
for kind in pair cross basket; do
  X=(); [ "$kind" = cross ] && X=(--panels-b "$W/panels-binance-um"); [ "$kind" = basket ] && X=(--basket-k 10)
  for r in $NEVER 240; do
    b --panels "$W/panels-bybit-linear" --kind "$kind" "${X[@]}" --max-jobs 8 --threads "$TH" --refresh "$r" --engine drift
  done
done
