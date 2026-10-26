#!/bin/bash
# TK-410 long4: свопы Sui-пулов SUI и US (cetus) за 01.09–14.10 (GraphQL, как tk393-sui-tron.py, окно T0/T1 из env). Выход /data/tk0393/tk410/long4-sui/sui/<pool>.jsonl; метка sui4.done.
#   alsched submit --kind net --name tk410-long4-sui --cores 2 --max-runtime 3h --est 60m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-long4-sui.sh
cd /data/tk0393/tk410 || exit 1; rm -f sui4.done
O=/data/tk0393/tk410/long4-sui; mkdir -p $O
printf 'chain,pool,dex\nsui,0x51e883ba7c0b566a26cbc8a94cd33eb0abd418a77cc1e60ad22fd9b1f29cd2ab,cetus\nsui,0x968f1f73b8e766e2ce0198c807d0c956aa384bfe64c98a18e56bfb970a13e7bb,cetus\n' > $O/pools.csv
sed 's/^T0, T1 = .*/T0, T1 = 1788220800, 1792022399/' /data/tk0393/tk393-sui-tron.py > $O/sui-long.py
python3 $O/sui-long.py sui $O/pools.csv $O/pools.csv $O > $O/run.log 2>&1
echo "rc=$? $(wc -l $O/sui/*.jsonl | tail -1) $(date +%T)" > sui4.done
