#!/bin/bash
# TK-410 хвост, проверка: только 08–14.10 (в 01–07.10 архив Gate = снимки книги, TK-388). map2 + agg1/agg2 -> out-long2, agg*-long2.txt, метка long2.done. cwd /data/tk0393/tk410
#   python3 /data/sched/alsched.py submit --cls prod --kind work --name tk410-long2 --cores 15 --mem 24 --max-runtime 1h --est 15m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-long2.sh
cd /data/tk0393/tk410; mkdir -p out-long2; rm -f long2.done
NX=${ALSCHED_CORES:-15}
printf '%s\n' Q SUE LAPTOP SI EMBER FONE AGENCY HOOKED ALLINU STAMP | xargs -P "$NX" -n1 sh -c 'GEV=/data/tk0393/tk410/gevlong D0S=1791417600 ND=7 /data/tk0380/npvenv/bin/python tk410-map2.py "$0" long-raw/"$0".csv out-long2/"$0" > out-long2/"$0".log 2>&1'
P=/data/tk0380/npvenv/bin/python
$P tk410-agg.py out-long2 long2 > agg1-long2.txt 2>&1
$P tk410-agg2.py out-long2 long2 > agg2-long2.txt 2>&1
echo "rc=$? $(ls out-long2/*curve.csv 2>/dev/null | wc -l) монет $(date +%T)" > long2.done
