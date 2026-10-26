#!/bin/bash
# TK-410 остаток v3: mid_raw как число (sed старых mid-r2), base AXIS KGEN VVV (извлечение->USD-середина), map2 всем 8 монетам. Метка rest3.done. cwd /data/tk0393/tk410
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; cd $S/tk410 || exit 1; mkdir -p mid-r2 out6 raw-r2; rm -f rest3.done
PY=/data/tk0380/npvenv/bin/python
sed -i -E 's/np\.float64\(([^)]*)\)/\1/' mid-r2/*.csv
tail -n +2 pools-r2.csv | grep -E "^(AXIS|KGEN|VVV)," | awk -F, '{print $1" "$2" "$3}' | xargs -P 3 -L1 sh -c 'SRC="/data/tk0393/sqd13?/base/part-*.jsonl" $0 tk410-extract.py $1 $2 $3 raw-r2/$1.csv > raw-r2/$1.log 2>&1' $PY
grep -E "^(AXIS|KGEN|VVV) " r2-list.txt | xargs -P 3 -L1 sh -c 'P=/data/tk0380/npvenv/bin/python; [ "$(wc -l < raw-r2/$0.csv)" -gt 100 ] || { echo "$0 raw<100 ($(wc -l < raw-r2/$0.csv))" > out6/$0.log; exit 0; }; $P tk410-w11-usd.py $0 raw-r2/$0.csv $1 mid-r2/$0.csv > out6/$0.ori.log 2>&1'
for c in BTC ETH LINK OP XAUT AXIS KGEN VVV; do [ -s mid-r2/$c.csv ] && echo $c; done | xargs -P "${ALSCHED_CORES:-15}" -L1 sh -c 'GEV=/data/tk0393/tk401/gev2all /data/tk0380/npvenv/bin/python tk410-map2.py $0 mid-r2/$0.csv out6/$0 > out6/$0.log 2>&1'
echo rc=$? > rest3.done
