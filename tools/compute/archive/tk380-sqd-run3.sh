#!/bin/bash
# TK-380: Base/ETH/Arbitrum через SQD параллельно, затем разбор в swaps.csv.gz; ALLDONE.json в /data/tk0380/sqd/
cd /data/tk0380
T0=1792022400; T1=1792713599
python3 -u tk380-sqd-evm.py base-mainnet pools-sqd.csv base /data/tk0380/sqd $T0 $T1 > sqd-base.log 2>&1 &
python3 -u tk380-sqd-evm.py ethereum-mainnet pools-sqd.csv ethereum /data/tk0380/sqd $T0 $T1 > sqd-eth.log 2>&1 &
python3 -u tk380-sqd-evm.py arbitrum-one pools-sqd.csv arbitrum /data/tk0380/sqd $T0 $T1 > sqd-arb.log 2>&1 &
wait
for c in base ethereum arbitrum; do
  python3 tk380-sqd-parse.py /data/tk0380/sqd/$c /data/tk0380/sqd/$c-swaps > parse-$c.log 2>&1
done
echo '{"done": true}' > /data/tk0380/sqd/ALLDONE.json
