#!/usr/bin/env bash
# TK-493 F6, G10 (добавка): скорость пути `--cells` против одиночных клеток и против tk486 на 7 140 заданиях (120 символов Binance, формирование).
# Запускать после g10_speed.sh (использует его каталог g10 и cells5.csv); на свободном Deck. Результаты выходов не читаются.
set -u
G=$HOME/xdl9/gates/g10; B=$HOME/xdl9/bin/statarb-xdl9-02c6a0df; O=$HOME/xdl9/bin/statarb-tk486-60e61cea
wait_free() { for i in $(seq 1 90); do l=$(cut -d" " -f1 /proc/loadavg); awk -v l=$l 'BEGIN{exit !(l<0.7)}' && return; sleep 5; done; }
wait_free
echo "нагрузка до: $(cat /proc/loadavg)"
cc() { name=$1; shift; { head -1 $G/cells5.csv; for c in "$@"; do grep "^$c," $G/cells5.csv; done; } > $G/cells_$name.csv; }
cc one c0; cc same c0 c1; cc two c0 c2; cc three c0 c2 c3
run_cells() {
  for bin in $B $O; do
    printf '%-9s %-22s ' "--cells" "$1 [$(( $(wc -l < $G/cells_$1.csv) - 1 )) кл.]"
    for rep in 1 2 3; do $bin statarb run --kind pair --cells $G/cells_$1.csv --jobs $G/jobs_pair.csv --panels $G/bin120 --min-span 1000 --threads 8 --daily-out $G/tmp.csv 2>&1 | grep -o "счёт [0-9.]* с" | tr "\n" " "; done
    echo "<- $(basename $bin | cut -c1-24)"
  done
}
for n in one same two three; do run_cells $n; done
printf '%-9s %-22s ' "--cells" "five [5 кл.]"
for bin in $B $O; do for rep in 1 2 3; do $bin statarb run --kind pair --cells $G/cells5.csv --jobs $G/jobs_pair.csv --panels $G/bin120 --min-span 1000 --threads 8 --daily-out $G/tmp.csv 2>&1 | grep -o "счёт [0-9.]* с" | tr "\n" " "; done; echo "<- $(basename $bin | cut -c1-24)"; done
for c in c0 c1 c2 c3 c4; do
  l=$(grep "^$c," $G/cells5.csv); IFS=, read -r cell w mo bf ez xz sz mh rf <<< "$l"; bfl=""; [ -n "$bf" ] && bfl="--beta-fixed $bf"
  for bin in $B $O; do
    printf '%-9s %-22s ' "--cell" "$c (W=$w refresh=$rf)"
    for rep in 1 2 3; do $bin statarb run --kind pair --cell $cell --window $w --min-obs $mo $bfl --entry-z $ez --exit-z $xz --stop-z $sz --max-hold $mh --refresh $rf --jobs $G/jobs_pair.csv --panels $G/bin120 --min-span 1000 --threads 8 --daily-out $G/tmp.csv 2>&1 | grep -o "счёт [0-9.]* с" | tr "\n" " "; done
    echo "<- $(basename $bin | cut -c1-24)"
  done
done
rm -f $G/tmp.csv
echo "нагрузка после: $(cat /proc/loadavg)"
