#!/bin/bash
# TK-410 добор 6 USD-монет, шаг 2 — середина пула (extract) -> map2 на 15 ядрах. Метка rest.done. cwd /data/tk0393/tk410
export PYTHONUTF8=1 LC_ALL=C.UTF-8
cd /data/tk0393/tk410 || exit 1; mkdir -p out4 mid-rest
sed "s#/data/tk0393/sqd10/#/data/tk0393/sqd11/#" tk410-extract-all.py > tk410-extract-rest.py
/data/tk0380/npvenv/bin/python tk410-extract-rest.py rest-pools.csv mid-rest > extrest.log 2>&1
: > list-rest.txt
for f in mid-rest/*__*.csv; do
  [ "$(wc -l < "$f")" -gt 100 ] || continue
  b=$(basename "$f" .csv); pool=${b#*__}
  coin=$(grep -i ",$pool," rest-pools.csv | head -1 | cut -d, -f1)
  [ -d /data/tk0393/tk401/gev2all/$coin ] && echo "$coin $f" >> list-rest.txt
done
cat list-rest.txt | xargs -P "${ALSCHED_CORES:-15}" -L1 sh -c 'GEV=/data/tk0393/tk401/gev2all /data/tk0380/npvenv/bin/python tk410-map2.py "$0" "$1" out4/"$0" > out4/"$0".log 2>&1'
echo rc=$? > rest.done
