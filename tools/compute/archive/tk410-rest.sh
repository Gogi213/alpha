#!/bin/bash
# TK-410 добор «22 монеты без файла»: 6 монет с USD-котировкой (AEON CFX IOTA bsc, AIGENSYN eth, GMT polygon, NXPC avalanche):
# SQD Swap+Sync 15-22.10 -> середина (extract-all) -> map2. Метка rest.done. cwd /data/tk0393/tk410
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd11; T0=1792022400; T1=1792713599
cd $S/tk410 || exit 1; mkdir -p "$R" out4 mid-rest
echo "coin,chain,pool" > rest-pools.csv
grep -E "^(AEON|CFX|IOTA|AIGENSYN|GMT|NXPC)," cover.csv | awk -F, '{print $1","$3","$4}' >> rest-pools.csv
for pair in bsc:binance-mainnet ethereum:ethereum-mainnet polygon:polygon-mainnet avalanche:c-mainnet; do
  c=${pair%%:*}; ds=${pair##*:}
  python3 -u tk410-sqd-evm.py $ds rest-pools.csv $c "$R" $T0 $T1 > "$R/$c.log" 2>&1 &
done
wait
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
