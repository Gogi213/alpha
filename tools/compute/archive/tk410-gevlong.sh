#!/usr/bin/env bash
# TK-410 хвост: gev2 (событийный BBO фьюча Gate) 01.09–14.10 для монет практиков (книги /srv/alpha-sda/tk387-gate) -> /data/tk0393/tk410/gevlong/<COIN>/<YYYYMMDDHH>.npy
#   alsched submit --kind gate --name tk410-gevlong --cores 15 --max-runtime 1h --est 15m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-gevlong.sh
set -uo pipefail
O=/data/tk0393/tk410; OB=/srv/alpha-sda/tk387-gate/futures_usdt/orderbooks; PY=/data/tk0380/npvenv/bin/python
rm -f $O/gevlong.done; mkdir -p $O/gevlong
for c in Q SUE LAPTOP AGENCY ALLINU EMBER FONE HOOKED SI STAMP PAID; do
  echo "$(ls $OB/*/${c}_USDT-*.csv.gz 2>/dev/null | xargs -r du -cb | tail -1 | cut -f1) $c"
done | sort -rn | cut -d' ' -f2 > $O/gevlong-units.txt
NX=${ALSCHED_CORES:-15}
xargs -a $O/gevlong-units.txt -P$NX -n1 sh -c 'fs=$(ls '$OB'/*/${1}_USDT-*.csv.gz | sort); $0 /data/tk0402/tk402-gate-bbo-ev.py '$O'/gevlong "$1" $fs >/dev/null' $PY; rc=$?
echo "rc=$rc монет $(wc -l < $O/gevlong-units.txt) с npy $(ls $O/gevlong | wc -l) $(date +%T)" > $O/gevlong.done
