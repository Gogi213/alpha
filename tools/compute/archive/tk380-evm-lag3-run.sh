#!/bin/bash
# TK-380: lag3 на ETH/Base/Arb параллельно, затем сводка; метка SUMM2.done
cd /data/tk0380
for ch in ethereum base arbitrum; do
  ( python3 -u tk380-evm-lag3.py $ch /data/tk0380/lag3-$ch > /data/tk0380/lag3-$ch.log 2>&1 && python3 tk380-bsc-lag3-summ.py /data/tk0380/lag3-$ch /data/tk0380/lag3-$ch/agg.json > /data/tk0380/lag3-$ch/agg.txt 2>&1 ) &
done
wait
echo done > /data/tk0380/SUMM2.done
