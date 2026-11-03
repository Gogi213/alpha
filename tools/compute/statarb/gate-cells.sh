#!/usr/bin/env bash
# TK-486: гейт «байт в байт» многоклеточного прохода — `statarb run --cells` нового бинарника против прежнего
# (alpha-run-plain), который считает клетки по одной: суточный csv и сделки всех заданий обязаны совпасть побайтно
# (прежние прогоны склеены в порядке файла клеток, как пишет --cells). Клетки — параметры проверки, не назначение стратегии.
#   gate-cells.sh <новый бинарь> <прежний бинарь> <каталог панелей> <jobs.csv y,x,from_day,to_day> <потоки> [рабочий каталог]
set -eu
NEW="${1:?новый бинарь}"; PREV="${2:?прежний бинарь}"; PAN="${3:?каталог панелей}"; JOBS="${4:?jobs.csv}"; TH="${5:?потоки}"
W="${6:-/data/tk0486/gate-cells}"
mkdir -p "$W"
n=$(($(wc -l < "$JOBS") - 1)); [ "$n" -ge 1 ] || { echo "gate-cells: в $JOBS нет заданий"; exit 2; }
ALL=$(seq -s, 0 $((n - 1)))
# метка,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh — две группы (window 240 / 360)
cat > "$W/cells.csv" <<'CELLS'
cell,window,min_obs,beta_fixed,entry_z,exit_z,stop_z,max_hold,refresh
g1,240,240,,2,0.5,inf,1440,240
g2,240,240,,2.5,0.2,4,720,240
g3,360,360,,2,0.5,inf,1440,0
g4,360,360,,3,0.3,5,0,0
CELLS
rm -f "$W/prev-daily.csv" "$W/prev-trades.csv"
first=1
tail -n +2 "$W/cells.csv" | while IFS=, read -r c win mo bf ez xz sz mh rf; do
  extra=""; [ -n "$bf" ] && extra="--beta-fixed $bf"
  # shellcheck disable=SC2086
  "$PREV" statarb run --kind pair --cell "$c" --jobs "$JOBS" --panels "$PAN" \
    --daily-out "$W/p-$c-daily.csv" --trades-out "$W/p-$c-trades.csv" --trade-jobs "$ALL" \
    --min-span 1440 --threads "$TH" --window "$win" --min-obs "$mo" --entry-z "$ez" --exit-z "$xz" --stop-z "$sz" \
    --max-hold "$mh" --refresh "$rf" $extra | tee "$W/p-$c.log"
done
for f in daily trades; do
  out="$W/prev-$f.csv"; first=1
  for c in $(tail -n +2 "$W/cells.csv" | cut -d, -f1); do
    if [ "$first" = 1 ]; then cat "$W/p-$c-$f.csv" > "$out"; first=0; else tail -n +2 "$W/p-$c-$f.csv" >> "$out"; fi
  done
done
"$NEW" statarb run --kind pair --cells "$W/cells.csv" --jobs "$JOBS" --panels "$PAN" \
  --daily-out "$W/new-daily.csv" --trades-out "$W/new-trades.csv" --trade-jobs "$ALL" \
  --min-span 1440 --threads "$TH" | tee "$W/new.log"
rc=0
for f in daily trades; do
  if cmp -s "$W/new-$f.csv" "$W/prev-$f.csv"; then
    echo "ГЕЙТ cells $f: байт в байт ($(wc -l < "$W/new-$f.csv") строк)"
  else
    echo "ГЕЙТ cells $f: РАЗЛИЧИЕ"; rc=1
  fi
done
exit "$rc"
