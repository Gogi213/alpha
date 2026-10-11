#!/usr/bin/env bash
# TK-028: базовый замер суток на деке — три отрезка (основной / t9 / Г-86) по очереди, каждый отдельным юнитом
# с учётом ЦП/памяти (Consumed/memory peak — в journalctl --user, юниты tk028-<тег>-p<n>). THREADS=n — --threads у отрезка 1.
# tk028-base.sh <СУТКИ> <тег>   дом замера ~/alpha/epochs/e-augbench (b5 свой), вывод ~/alpha/tk028/<тег>/
set -uo pipefail
D="${1:?сутки}"; TAG="${2:?тег}"
A="$HOME/alpha"; H="$A/epochs/e-augbench"; SC="$A/tmp-p07/cells-by-day/jall-aug-$D.sh"
O="$A/tk028/$TAG"; mkdir -p "$O"; rm -f "$O"/seg*.sh "$O/DONE"
awk -v v="$O" 'BEGIN { k = 0 } /^set -e$/ { k++ } { print > (v "/seg" k ".sh") }' "$SC"
sed -i -E "s#\.cellstmp-$D#.g86tmp-$D#g" "$O/seg3.sh"
[ -n "${THREADS:-}" ] && sed -i -E "s/ --threads [0-9]+ / --threads $THREADS /" "$O/seg1.sh"
grep -o -- "--threads [0-9]*" "$O/seg1.sh" > "$O/threads.txt"
cd "$H" || exit 2
for n in 1 2 3; do
  t0=$(date +%s.%N)
  systemd-run --user --wait --collect --quiet -u "tk028-$TAG-p$n" -p MemoryAccounting=yes -p CPUAccounting=yes \
    -p WorkingDirectory="$H" -p StandardOutput=file:"$O/p$n.out" -p StandardError=file:"$O/p$n.err" \
    -P bash "$O/seg$n.sh" > "$O/p$n.props" 2>&1
  rc=$?
  t1=$(date +%s.%N)
  echo "p$n rc=$rc wall=$(echo "$t1 - $t0" | bc -l 2>/dev/null || python3 -c "print($t1-$t0)")" >> "$O/summary.txt"
done
find b5 -path "*$D*" -type f ! -name "*.log" | LC_ALL=C sort | xargs sha256sum > "$O/manifest.sha"
# manifest.body.sha — содержимое без строк-заголовков "#" (в них пишется threads=n и т.п.)
find b5 -path "*$D*" -type f ! -name "*.log" | LC_ALL=C sort | while read -r f; do printf '%s  %s
' "$(grep -v '^#' "$f" | sha256sum | cut -d' ' -f1)" "$f"; done > "$O/manifest.body.sha"
wc -l < "$O/manifest.sha" >> "$O/summary.txt"
echo "$(date -Is)" > "$O/DONE"
