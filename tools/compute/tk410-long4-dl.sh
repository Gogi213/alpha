#!/bin/bash
# TK-410 long4: книги Gate фьюч (download.gatedata.org) 01.09–14.10 для SUI US OP ETH BTC GT PAID -> /srv/alpha-sda/tk410-gate/futures_usdt/orderbooks/YYYYMM. Метка dl4.done. Докачка: готовое пропускается; не-200 -> log/miss.tsv.
#   alsched submit --kind net --name tk410-long4-dl --cores 4 --max-runtime 1h --est 10m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-long4-dl.sh
set -u
R=/srv/alpha-sda/tk410-gate; mkdir -p $R/log $R/futures_usdt/orderbooks/202609 $R/futures_usdt/orderbooks/202610
for c in SUI US OP ETH BTC GT PAID; do
  for d in $(seq 0 43); do day=$(date -u -d "2026-09-01 +$d day" +%Y%m%d); for h in $(seq -w 0 23); do echo "${c}_USDT $day$h"; done; done
done > $R/log/tasks.txt
dl() {
  c=$1; h=$2; f=$3/futures_usdt/orderbooks/${h:0:6}/$c-$h.csv.gz
  [ -s "$f" ] && return 0
  for try in 1 2 3; do
    code=$(curl -s -m 300 -o "$f.part" -w '%{http_code}' "https://download.gatedata.org/futures_usdt/orderbooks/${h:0:6}/$c-$h.csv.gz")
    if [ "$code" = 200 ] && gzip -t "$f.part" 2>/dev/null; then mv "$f.part" "$f"; return 0; fi
    [ "$code" = 404 ] && break; sleep $((try*5))
  done
  printf '%s\t%s\t%s\n' "$c" "$h" "$code" >> $3/log/miss.tsv
}
export -f dl
xargs -a $R/log/tasks.txt -P4 -L1 bash -c 'dl "$0" "$1" '$R
echo "rc=$? files=$(find $R/futures_usdt -name '*.csv.gz' | wc -l) miss=$(wc -l < $R/log/miss.tsv 2>/dev/null) $(date +%T)" > /data/tk0393/tk410/dl4.done
