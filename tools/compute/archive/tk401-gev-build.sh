#!/usr/bin/env bash
# TK-401 R7: gev2 (событийный BBO фьюча Gate) для 46 монет пулов, 15–22.10, из orderbooks tk401-ob46 (dl.done). Выход /data/tk0393/tk401/gev2/<COIN>/<YYYYMMDDHH>.npy
#   alsched submit --kind gate --name tk401-gev --cores 15 --max-runtime 1h --est 10m --cwd /data/tk0393/tk401/ob -- bash /data/tk0393/tk401/ob/gev-build.sh
set -uo pipefail
O=/data/tk0393/tk401; OB=/srv/alpha-sda/tk379-ob/futures_usdt/orderbooks/202610; PY=/data/tk0380/npvenv/bin/python
rm -f $O/ob/gev.done; mkdir -p $O/gev2
sed 's/_USDT$//' $O/ob/list46.txt | while read c; do
  fs=$(ls $OB/${c}_USDT-20261015*.csv.gz $OB/${c}_USDT-2026101[6-9]*.csv.gz $OB/${c}_USDT-2026102[0-2]*.csv.gz 2>/dev/null | tr '\n' ' ')
  [ -n "$fs" ] || { echo "$c нет книги Gate" >> $O/ob/gev-missing.txt; continue; }
  echo "$(du -cb $fs | tail -1 | cut -f1) $c"
done | sort -rn | cut -d' ' -f2 > $O/ob/gev-units.txt
xargs -a $O/ob/gev-units.txt -P15 -n1 sh -c '$0 /data/tk0402/tk402-gate-bbo-ev.py '$O'/gev2 "$1" >/dev/null' $PY; rc=$?
echo "rc=$rc монет $(wc -l < $O/ob/gev-units.txt) с npy $(ls $O/gev2 | wc -l) $(date +%T)" > $O/ob/gev.done
