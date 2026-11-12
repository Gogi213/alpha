#!/bin/bash
# TK-410: книга СПОТА Gate (download.gatedata.org/spot/orderbooks/YYYYMM/ПАРА-YYYYMMDDHH.csv.gz), 15–22.10 UTC, 4 потока,
# готовое пропускается, 404 → log/miss.tsv. Корень /srv/alpha-sda/tk410-spot-ob. Список — пары через пробел в $1 (файл, строка на пару).
#   alsched submit --kind net --name tk410-spot-ob --max-runtime 3h --est 40m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-spot-ob-dl.sh /data/tk0393/tk410/spot-p.txt
set -u
list=$1; root=/srv/alpha-sda/tk410-spot-ob; dir=$root/spot/orderbooks/202610; mkdir -p "$dir" "$root/log"
free=$(df -BG --output=avail "$root" | tail -1 | tr -dc 0-9); [ "$free" -ge 100 ] || { echo "мало места: ${free} ГБ" >&2; exit 2; }
tasks=$root/log/tasks-$(basename "$list").txt
for d in 15 16 17 18 19 20 21 22; do for h in $(seq -w 0 23); do while read -r c; do echo "$c 202610$d$h"; done < "$list"; done; done > "$tasks"
dl() {
  c=$1; h=$2; f=$3/$c-$h.csv.gz; [ -s "$f" ] && return 0
  code=$(curl -s -m 600 --retry 4 --retry-delay 5 -o "$f.part" -w '%{http_code}' "https://download.gatedata.org/spot/orderbooks/${h:0:6}/$c-$h.csv.gz")
  if [ "$code" = 200 ] && gzip -t "$f.part" 2>/dev/null; then mv "$f.part" "$f"; else rm -f "$f.part"; printf '%s\t%s\t%s\n' "$c" "$h" "$code" >> "$4/log/miss.tsv"; fi
}
export -f dl
echo "start $(date +%T) tasks=$(wc -l < "$tasks") list=$list" >> "$root/log/run.log"
xargs -a "$tasks" -P4 -L1 bash -c 'dl "$0" "$1" '"$dir $root"
echo "end rc=$? $(date +%T) files=$(ls "$dir" | grep -c 'csv.gz$') miss=$(wc -l < "$root/log/miss.tsv" 2>/dev/null)" >> "$root/log/run.log"
echo DONE > "$root/$(basename "$list").done"
