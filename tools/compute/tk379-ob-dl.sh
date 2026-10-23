#!/bin/bash
# TK-379: стакан фьючерса Gate (download.gatedata.org/futures_usdt/orderbooks/YYYYMM/КОНТРАКТ-YYYYMMDDHH.csv.gz),
# 15–22.10 UTC, 4 потока, готовые файлы пропускаются, 404 → log/miss.tsv (контракт, час, код).
# usage: tk379-ob-dl.sh <список контрактов> <корень> [файл-rc, дождаться перед стартом]
set -u
list=$1; root=$2; waitrc=${3:-}
[ -n "$waitrc" ] && while [ ! -e "$waitrc" ]; do sleep 30; done
dir=$root/futures_usdt/orderbooks/202610; mkdir -p "$dir" "$root/log"
free=$(df -BG --output=avail "$root" | tail -1 | tr -dc 0-9)
[ "$free" -ge 250 ] || { echo "мало места: ${free} ГБ" >&2; exit 2; }
tasks=$root/log/tasks.txt
for d in ${DAYS:-15 16 17 18 19 20 21 22}; do for h in $(seq -w 0 23); do
  while read -r c; do echo "$c 202610$d$h"; done < "$list"
done; done > "$tasks"
dl() {
  c=$1; h=$2; f=$3/$c-$h.csv.gz
  [ -s "$f" ] && return 0
  code=$(curl -sf -m 600 --retry 6 --retry-delay 10 -o "$f.part" -w '%{http_code}' \
    "https://download.gatedata.org/futures_usdt/orderbooks/${h:0:6}/$c-$h.csv.gz")
  if [ "$code" = 200 ] && gzip -t "$f.part" 2>/dev/null; then mv "$f.part" "$f"
  else printf '%s\t%s\t%s\n' "$c" "$h" "$code" >> "$4/log/miss.tsv"; fi
}
export -f dl
echo "tk379-ob start $(date +%T) tasks=$(wc -l < "$tasks")" >> "$root/log/run.log"
xargs -a "$tasks" -P4 -L1 bash -c 'dl "$0" "$1" '"$dir $root"
echo "tk379-ob end rc=$? $(date +%T) files=$(ls "$dir" | grep -c 'csv.gz$') miss=$(wc -l < "$root/log/miss.tsv" 2>/dev/null)" >> "$root/log/run.log"
