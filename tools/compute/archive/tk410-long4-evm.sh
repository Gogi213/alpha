#!/bin/bash
# TK-410 long4: свопы+Sync EVM-пулов OP (optimism, velodrome) и ETH (bsc, pancake WBNB) за 01.09–14.10 через SQD -> /data/tk0393/sqd14/<chain>; середина -> mid-long4/. Метка evm4.done. cwd /data/tk0393/tk410
#   alsched submit --kind net --name tk410-long4-evm --cores 2 --max-runtime 3h --est 60m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-long4-evm.sh
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd14; cd $S/tk410 || exit 1; rm -f evm4.done; mkdir -p $R mid-long4
printf 'coin,chain,pool\nOP,optimism,0x4DC22588Ade05C40338a9D95A6da9dCeE68Bcd60\nETH,bsc,0x62Fcb3C1794FB95BD8B1A97f6Ad5D8a7e4943a1e\n' > long4-pools.csv
T0=1788220800; T1=1792022399
python3 -u tk410-sqd-evm.py optimism-mainnet long4-pools.csv optimism $R $T0 $T1 >> $R/optimism.log 2>&1 &
python3 -u tk410-sqd-evm.py binance-mainnet long4-pools.csv bsc $R $T0 $T1 >> $R/bsc.log 2>&1 &
wait
sed "s#/data/tk0393/sqd10/#/data/tk0393/sqd14/#" tk410-extract-all.py > tk410-extract-long4.py
/data/tk0380/npvenv/bin/python tk410-extract-long4.py long4-pools.csv mid-long4 > extlong4.log 2>&1
echo "rc=$? $(wc -l mid-long4/*.csv | tail -1) $(date +%T)" > evm4.done
