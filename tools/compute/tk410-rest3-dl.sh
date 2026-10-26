#!/bin/bash
# TK-410 остаток v3 (Судья 16:38): Base AXIS KGEN VVV, 4 сегмента net; топики Aerodrome v2 Swap/Sync добавлены. По концу подаёт rest3-proc. cwd /data/tk0393/tk410
export PYTHONUTF8=1 LC_ALL=C.UTF-8
S=/data/tk0393; cd $S/tk410 || exit 1; rm -f rest3-dl.done
for seg in "sqd13a 52616527 52702926" "sqd13b 52702927 52789326" "sqd13c 52789327 52875726" "sqd13d 52875727 52962126"; do
  set -- $seg; d=$S/$1/base; mkdir -p $d; rm -f $d/DONE.json $d/part-*
  echo "$2 $3" > $d/cursor
  python3 -u tk410-sqd-evm.py base-mainnet pools-base.csv base $S/$1 1792022400 1792713599 >> $S/$1/base.log 2>&1 &
done
wait
n=0; for d in sqd13a sqd13b sqd13c sqd13d; do [ -f $S/$d/base/DONE.json ] && n=$((n+1)); done
echo "segments_done=$n" > rest3-dl.done
[ "$n" = 4 ] || exit 1
python3 /data/sched/alsched.py submit --name tk410-rest3-proc --max-runtime 1h --est 25m --cores 15 --cwd $S/tk410 -- bash $S/tk410/rest3-proc.sh > rest3-proc-submit.log 2>&1
