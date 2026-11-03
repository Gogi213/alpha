#!/usr/bin/env bash
# TK-486: гейт «байт в байт» — `statarb run` нового бинарника против прежнего (alpha-run-plain) на одних заданиях:
# суточный csv и сделки всех заданий обязаны совпасть побайтно (метод не менялся, ускорение — только скорость).
# Параметры клетки — параметры проверки (как в gate.sh), не назначение стратегии.
#   gate-prev.sh <новый бинарь> <прежний бинарь> <каталог панелей> <jobs.csv y,x,from_day,to_day> <потоки> [рабочий каталог]
set -eu
NEW="${1:?новый бинарь}"; PREV="${2:?прежний бинарь}"; PAN="${3:?каталог панелей}"; JOBS="${4:?jobs.csv}"; TH="${5:?потоки}"
W="${6:-/data/tk0486/gate-prev}"
mkdir -p "$W"
n=$(($(wc -l < "$JOBS") - 1)); [ "$n" -ge 1 ] || { echo "gate-prev: в $JOBS нет заданий"; exit 2; }
ALL=$(seq -s, 0 $((n - 1)))
rc=0
# клетки: (метка, entry, exit, stop, max-hold, refresh)
for cell in "g1 2 0.5 inf 1440 240" "g2 2.5 0.2 4 720 0"; do
  set -- $cell
  for who in new prev; do
    BIN="$NEW"; [ "$who" = prev ] && BIN="$PREV"
    "$BIN" statarb run --kind pair --cell "$1" --jobs "$JOBS" --panels "$PAN" \
      --daily-out "$W/$1-$who-daily.csv" --trades-out "$W/$1-$who-trades.csv" --trade-jobs "$ALL" \
      --min-span 1440 --threads "$TH" --window 240 --min-obs 240 --entry-z "$2" --exit-z "$3" --stop-z "$4" \
      --max-hold "$5" --refresh "$6" | tee "$W/$1-$who.log"
  done
  for f in daily trades; do
    if cmp -s "$W/$1-new-$f.csv" "$W/$1-prev-$f.csv"; then
      echo "ГЕЙТ $1 $f: байт в байт ($(wc -l < "$W/$1-new-$f.csv") строк)"
    else
      echo "ГЕЙТ $1 $f: РАЗЛИЧИЕ"; rc=1
    fi
  done
done
exit "$rc"
