#!/bin/bash
# TK-620, П-42 §13.31 п. 8.4: 28 клеток + контроль (L_base, S_base) на окнах wa / wb / wc одним вызовом движка на окно (по умолчанию — вселенная 566).
# Запуск на srv: systemd-run --unit=tk620-run --nice=19 --property=CPUWeight=1 bash /data/p42-tk620/code/tools/p42/tk620_run.sh <БИНАРЬ> [корень-выхода=/data/p42-tk620/out] [файл-монет=/data/p42-t566/symbols.txt] [корень-данных=/data/p42-tk599]
# Окно за окном; код возврата окна — в <корень-выхода>.rc; любой ненулевой — остановка. Сутки позже 13.11 в окнах не лежат (данные до 13.11).
set -u
BIN="${1:?бинарь}"; OUT="${2:-/data/p42-tk620/out}"; SYMF="${3:-/data/p42-t566/symbols.txt}"; DATA="${4:-/data/p42-tk599}"
CODE=/data/p42-tk620/code
VAR=$CODE/docs/research/freeze/tk620-variants.txt
[ "$(sha256sum "$VAR" | cut -c1-64)" = 4971e7200592e21ac25cd45a82f52316b5dcd51782bf4936b946e39258fe9104 ] || { echo "variants sha256 не совпал" > "$OUT.rc"; exit 2; }
SYMS="$(cat "$SYMF")"
mkdir -p "$OUT.log" "$OUT.sc"
: > "$OUT.rc"
declare -A T0=( [wa]=2026-06-17 [wb]=2026-07-25 [wc]=2026-09-01 )   # = retitr_full.WINDOWS (проверяется до запуска)
for w in wa wb wc; do
  mkdir -p "$OUT/$w"
  P42_T0=${T0[$w]} "$BIN" breakout tick --cache "$DATA/$w/cache" --trades "$DATA/$w/aggtrades" \
    --variants "$VAR" --symbols "$SYMS" --out "$OUT/$w" --threads 4 \
    --tick-store "$DATA/tick-store" --tick-store-cap-gb 3 --tick-store-min-free-gb 5 \
    --sig-cache "$OUT.sc" >> "$OUT.log/run-$w.log" 2>&1
  rc=$?
  echo "$w $rc" >> "$OUT.rc"
  if [ "$rc" -ne 0 ]; then exit "$rc"; fi
done
echo done >> "$OUT.rc"
