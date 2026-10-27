#!/bin/bash
# TK-428 п.2 (G-017): Gate фандинг-архив futures_usdt/{funding_updates,funding_applies} 202601–202607 (download.gatedata.org), все фьючерсы gate-list.tsv.
# Выход: $ROOT/{updates,applies}/МЕСЯЦ/СИМВОЛ.csv.gz; не-200 (контракта не было / не листился в месяце) -> $ROOT/log/miss.tsv. Готовое пропускается.
# Использование: tk428-gate-funding.sh   (ROOT=/srv/alpha-sda/tk428/gate-funding, PAR=8, LIST=/data/tk0393/tk423/gate-list.tsv)
set -u
root=${ROOT:-/srv/alpha-sda/tk428/gate-funding}; list=${LIST:-/data/tk0393/tk423/gate-list.tsv}
mkdir -p "$root/log"
dl() {
  kind=$1; mon=$2; c=$3; root=$4
  f=$root/$kind/$mon/$c.csv.gz; [ -s "$f" ] && return 0
  mkdir -p "$root/$kind/$mon"
  code=$(curl -s -m 300 --retry 4 --retry-delay 5 -o "$f.part" -w '%{http_code}' "https://download.gatedata.org/futures_usdt/funding_$kind/$mon/$c-$mon.csv.gz")
  if [ "$code" = 200 ] && gzip -t "$f.part" 2>/dev/null; then mv "$f.part" "$f"; else rm -f "$f.part"; printf '%s\t%s\t%s\t%s\n' "$kind" "$mon" "$c" "$code" >> "$root/log/miss.tsv"; fi
}
export -f dl
echo "start $(date +%T)" >> "$root/log/run.log"
for kind in updates applies; do
  for mon in 202601 202602 202603 202604 202605 202606 202607; do
    awk -F'\t' '$1=="fut" {print $2}' "$list" | xargs -P"${PAR:-8}" -I{} bash -c 'dl "$0" "$1" "$2" "$3"' "$kind" "$mon" {} "$root"
  done
done
echo "end rc=$? $(date +%T) files=$(find "$root/updates" "$root/applies" -name '*.csv.gz' | wc -l) miss=$(wc -l < "$root/log/miss.tsv" 2>/dev/null)" >> "$root/log/run.log"
