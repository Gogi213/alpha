#!/bin/bash
# TK-410 хвост: tk410-map2.py по 10 монетам практиков за 01.09–14.10 (44 сут., окно 24 ч). Метка long.done. cwd /data/tk0393/tk410
#   python3 /data/sched/alsched.py submit --cls prod --kind work --name tk410-long --cores 15 --mem 24 --max-runtime 2h --est 30m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-long.sh
cd /data/tk0393/tk410; mkdir -p out-long; rm -f long.done
while pgrep -f 'tk410-extract.py Q ' >/dev/null || [ ! -f gevlong.done ]; do sleep 10; done
NX=${ALSCHED_CORES:-15}
printf '%s\n' Q SUE LAPTOP SI EMBER FONE AGENCY HOOKED ALLINU STAMP | xargs -P "$NX" -n1 sh -c 'GEV=/data/tk0393/tk410/gevlong D0S=1788220800 ND=44 /data/tk0380/npvenv/bin/python tk410-map2.py "$0" long-raw/"$0".csv out-long/"$0" > out-long/"$0".log 2>&1'
echo "rc=$? $(ls out-long/*curve.csv 2>/dev/null | wc -l) монет $(date +%T)" > long.done
