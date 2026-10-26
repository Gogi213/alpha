#!/bin/bash
# TK-410 ОДНА заявка остатка (Судья 15:11): W11 BTC ETH LINK OP XAUT (USD через котировку gev2, ориентация по корреляции) + base AXIS KGEN VVV
# (SQD base 15-22.10, v3/Slipstream Swap + v2 Sync) -> USD-середина -> map2 15 ядер. Метка rest2.done. cwd /data/tk0393/tk410
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; cd $S/tk410 || exit 1; mkdir -p mid-r2 out5 sqd12 raw-r2; rm -f rest2.done
PY=/data/tk0380/npvenv/bin/python
echo "coin,chain,pool,quote" > pools-r2.csv
for c in BTC ETH LINK OP XAUT AXIS KGEN VVV; do
  grep -E "^$c," cover.csv | awk -F, '{print $1","$3","tolower($4)","$7}' >> pools-r2.csv
done
# закачка base (net) — только AXIS KGEN VVV
echo "coin,chain,pool" > pools-base.csv; grep -E "^(AXIS|KGEN|VVV)," pools-r2.csv | awk -F, '{print $1","$2","$3}' >> pools-base.csv
python3 -u tk410-sqd-evm.py base-mainnet pools-base.csv base $S/sqd12 1792022400 1792713599 > sqd12/base.log 2>&1
# извлечение сырья: W11-монеты из sqd*, base-монеты из sqd12
tail -n +2 pools-r2.csv | grep -v -E "^(AXIS|KGEN|VVV)," | awk -F, '{print $1" "$2" "$3}' | xargs -P 8 -L1 sh -c '$0 tk410-extract.py $1 $2 $3 raw-r2/$1.csv > raw-r2/$1.log 2>&1' $PY
tail -n +2 pools-r2.csv | grep -E "^(AXIS|KGEN|VVV)," | awk -F, '{print $1" "$2" "$3}' | xargs -P 3 -L1 sh -c 'SRC="/data/tk0393/sqd12/$1/blocks-*.jsonl" $0 tk410-extract.py $1 $2 $3 raw-r2/$1.csv > raw-r2/$1.log 2>&1' $PY
# USD-середина + карта
tail -n +2 pools-r2.csv | awk -F, '{q=$4; sub(/^W/,"",q); if(q=="WBNB")q="BNB"; if(q=="BNB"||q=="ETH"||q=="PAXG") print $1" "q; else if(q=="USDC"||q=="USDT") print $1" -"; else print $1" NOQ"}' > r2-list.txt
grep -v NOQ r2-list.txt | xargs -P "${ALSCHED_CORES:-15}" -L1 sh -c 'P=/data/tk0380/npvenv/bin/python; [ "$(wc -l < raw-r2/$0.csv)" -gt 100 ] || { echo "$0 raw<100" > out5/$0.log; exit 0; }; $P tk410-w11-usd.py $0 raw-r2/$0.csv $1 mid-r2/$0.csv > out5/$0.ori.log 2>&1 && GEV=/data/tk0393/tk401/gev2all $P tk410-map2.py $0 mid-r2/$0.csv out5/$0 > out5/$0.log 2>&1'
echo rc=$? > rest2.done
