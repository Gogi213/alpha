#!/usr/bin/env bash
# Судья 27.09: выборочная независимая сверка гейта T-31 — (д) фильтр реплея против точного T-29 без signal_index,
# (б) реплей против on, по нескольким суткам; и счётчики forms.csv эталона по дням записи.
cd ~/alpha
strip() { grep -av '^#' "$1" | awk -F, 'BEGIN{OFS=","} {$4=""; print}' | sort; }
for pair in "epochs/e-aug:2026-08-05" "epochs/e-aug:2026-08-21" "tmp-lsk0914.used-20260926/home:2026-09-09" "tmp-t29/rec:2026-09-17"; do
  h=${pair%%:*}; d=${pair#*:}
  a="$h/b5/t31-filt/$d/t-bid-btc4h-q1/rounds.csv"; b="$h/b5/t29-a60u100k/$d/t29-a60u100k/rounds.csv"
  if [ -f "$a" ] && [ -f "$b" ]; then
    if cmp -s <(strip "$a") <(strip "$b"); then r="равно"; else r="РАЗНОЕ"; fi
    echo "(д) $d: строк $(grep -avc '^#' "$a") / $(grep -avc '^#' "$b") — $r"
  else echo "(д) $d: нет файла ($a | $b)"; fi
  c="$h/b5/t31-replay/$d/t-bid-btc4h-q1/rounds.csv"; e="$h/b5/t31-on/$d/t-bid-btc4h-q1/rounds.csv"
  [ -f "$c" ] && [ -f "$e" ] && { cmp -s <(grep -av '^#' "$c") <(grep -av '^#' "$e") && echo "(б) $d: равно" || echo "(б) $d: РАЗНОЕ"; }
done
for d in 16 17 18 19 20 21 22 23 24 25 26; do
  f=b5/titrc-u500r/2026-09-$d/t-bid-btc4h-q1/forms.csv; [ -f "$f" ] && echo "09-$d $(grep -av '^#' $f | head -1 | cut -c1-80)"
done | head -3
