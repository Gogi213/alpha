#!/bin/bash
# TK-071 п.3 (судья 09.10 03:38, п.3): серия A=A на РЕАЛЬНОМ стенде d15 (бой-флаги e37, вход /dev/shm): 3 пары под нагрузкой (--iso 4,
# 12 ядер заняты crc32-нагрузкой tk071-pair.py) вперемешку с 3 парами в пустом окне (без --iso: всё производство заморожено). Запуск — юнитом на calc.
#   Выход: /data/tk071/series/summary.txt (по парам: wall/usage_usec/ok обеих сторон, Δ%, max |Δ| по парам ok:true×2, смещение нагрузка−пусто), done.
D=/data/tk071/series; S=/data/sched; A="python3 $S/alsched.py"; B=alpha-e36-idx
F="ALPHA_SKIP_SAME=1 ALPHA_SKIP_NOSIGNAL=1 ALPHA_FAST_HOLD=1 ALPHA_EVENT_STEPS=entry ALPHA_SIG_CACHE=3000000 ALPHA_APPROACH_BIN=1 ALPHA_ADMIT_CACHE=1 ALPHA_HOLDS_MEMO=1 ALPHA_ADMIT_SOA=1 ALPHA_BAND_COUNT_OFF=1 ALPHA_DIRECT_FEED=1 ALPHA_TOUCH_BIN=1"
mkdir -p $D; rm -f $D/done $D/stop $D/ids.txt   # LOAD_ID=<id> — не ставить нагрузку заново
LOAD=${LOAD_ID:-$($A submit --cls prod --name tk071-load --max-runtime 3h --cores 12 --mem 8 --cwd /data/tk071 -- bash -c "while [ ! -e $D/stop ]; do timeout 120 python3 /data/tk071/tk071-pair.py 12 100000000; done")
echo "load $LOAD" >> $D/ids.txt
waitjob() { while :; do s=$(python3 -c "import json,sys;print(json.load(open('$S/jobs/$1.json'))['state'])"); [ "$s" = done ] && return; sleep 10; done; }
run() { # $1 метка (L1a…), $2 "--iso 4 --mem 20" | ""
  local id; id=$($A submit --cls measure --name ser$1 --max-runtime 40m $2 --disk none --recompute --why "TK-071 п.3: серия A=A на стенде d15" --cwd /data/tk051 -- bash /data/tk051/stand.sh $B d15 TAG=ser$1 $F | tail -1)
  echo "$1 $id $(uptime | sed 's/.*load/load/')" >> $D/ids.txt; waitjob "$id"; }
sleep 60                                               # нагрузка встала на ядра
for k in 1 2 3; do
  run L${k}a "--iso 4 --mem 20"; run L${k}b "--iso 4 --mem 20"
  run E${k}a ""; run E${k}b ""
done
touch $D/stop
python3 /data/tk071/series-summary.py > $D/summary.txt 2>&1
touch $D/done
