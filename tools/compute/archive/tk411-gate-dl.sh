#!/bin/bash
# TK-411: месячные архивы сделок Gate за 2026-09 (futures_usdt/trades, spot/deals) -> /srv/alpha-sda/tk411/raw/{fut,spot}/ИНСТР.csv.gz.
# PAR потоков (по умолчанию 4; TK-423: PAR=16, 8 шли без 429 в TK-379), готовое пропускается, не-200 -> log/miss.tsv. Список — файл «рынок<TAB>инструмент<TAB>quanto» в $1.
#   alsched submit --kind net --name tk411-gate-dl --max-runtime 3h --est 40m --cwd /data/tk0393/tk411 -- bash /data/tk0393/tk411/tk411-gate-dl.sh /data/tk0393/tk411/gate-list.tsv
set -u
list=$1; root=${ROOT:-/srv/alpha-sda/tk411}; mon=${MON:-202609}; mkdir -p "$root/raw/fut" "$root/raw/spot" "$root/log"
free=$(df -BG --output=avail "$root" | tail -1 | tr -dc 0-9); [ "$free" -ge 30 ] || { echo "мало места: ${free} ГБ" >&2; exit 2; }
dl() {
  k=$1; c=$2; root=$3
  [ "$k" = fut ] && u="futures_usdt/trades" || u="spot/deals"
  f=$root/raw/$k/$c.csv.gz; [ -s "$f" ] && return 0
  code=$(curl -s -m 900 --retry 4 --retry-delay 5 -o "$f.part" -w '%{http_code}' "https://download.gatedata.org/$u/$mon/$c-$mon.csv.gz")
  if [ "$code" = 200 ] && gzip -t "$f.part" 2>/dev/null; then mv "$f.part" "$f"; else rm -f "$f.part"; printf '%s\t%s\t%s\n' "$k" "$c" "$code" >> "$root/log/miss.tsv"; fi
}
export -f dl; export mon
echo "start $(date +%T) n=$(wc -l < "$list")" >> "$root/log/run.log"
cut -f1,2 "$list" | xargs -P"${PAR:-4}" -L1 bash -c 'dl "$0" "$1" '"$root"
echo "end rc=$? $(date +%T) fut=$(ls "$root/raw/fut" | grep -c 'csv.gz$') spot=$(ls "$root/raw/spot" | grep -c 'csv.gz$') miss=$(wc -l < "$root/log/miss.tsv" 2>/dev/null)" >> "$root/log/run.log"
echo DONE > "$root/dl.done"
