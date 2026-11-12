#!/bin/bash
# TK-410 long4 evm2: OP (optimism) и ETH (bsc) через SQD сегментами параллельно (остаток от курсоров sqd14 + голова BSC 119.2M–120.5M, которой не было: старт искал блок на 07.09). Середина -> mid-long4/. Метка evm4.done.
#   alsched submit --kind net --name tk410-long4-evm2 --cores 2 --max-runtime 4h --est 90m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-long4-evm2.sh
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; R=$S/sqd14; RS=$S/sqd14s; cd $S/tk410 || exit 1; rm -f evm4.done; mkdir -p $RS mid-long4
T0=1788220800; T1=1792022399; PIDS=""
run() { python3 -u tk410-sqd-evm.py $1 long4-pools.csv $2 $3 $T0 $T1 >> $3/$2.log 2>&1 & PIDS="$PIDS $!"; }
split() {  # chain K : режет остаток курсора основного каталога на K сегментов; K-1 уходят в sqd14s/<chain>-<i>
  local c=$1 K=$2 b e S i; read b e < $R/$c/cursor
  if [ ! -e $RS/$c.split ]; then
    for i in $(seq 1 $((K-1))); do S=$((b + i*(e-b+1)/K)); e2=$((b + (i+1)*(e-b+1)/K - 1)); [ $i -eq $((K-1)) ] && e2=$e
      mkdir -p $RS/$c-$i/$c; echo "$S $e2" > $RS/$c-$i/$c/cursor; done
    echo "$b $((b + (e-b+1)/K - 1))" > $R/$c/cursor; touch $RS/$c.split
  fi
  run $4 $c $R; for i in $(seq 1 $((K-1))); do run $4 $c $RS/$c-$i; done
}
split optimism 4 x optimism-mainnet
split bsc 4 x binance-mainnet
mkdir -p $RS/bsc-h/bsc; [ -e $RS/bsc-h/bsc/cursor ] || echo "119200000 120508382" > $RS/bsc-h/bsc/cursor
run binance-mainnet bsc $RS/bsc-h
wait $PIDS
sed "s#sorted(glob.glob(f'/data/tk0393/sqd10/{chain}/part-\*.jsonl'))#sorted(glob.glob(f'/data/tk0393/sqd14*/{chain}/part-*.jsonl') + glob.glob(f'/data/tk0393/sqd14s/*/{chain}/part-*.jsonl'), key=lambda p: json.loads(open(p).readline())['header']['number'])#" tk410-extract-all.py > tk410-extract-long4.py
/data/tk0380/npvenv/bin/python tk410-extract-long4.py long4-pools.csv mid-long4 > extlong4.log 2>&1
echo "rc=$? $(wc -l mid-long4/*.csv | tail -1) $(date +%T)" > evm4.done
