#!/bin/bash
# TK-410 П-31 бой Q1: tk410-map2.py по монетам, NX процессов. Метка battle2.done. cwd /data/tk0393/tk410
cd /data/tk0393/tk410; mkdir -p out2
/data/tk0380/npvenv/bin/python tk410-run.py > list.txt 2> list.err
NX=${ALSCHED_CORES:-15}
cat list.txt | xargs -P "$NX" -L1 sh -c 'GEV=/data/tk0393/tk401/gev2all /data/tk0380/npvenv/bin/python tk410-map2.py "$0" "$1" out2/"$0" > out2/"$0".log 2>&1'
touch battle2.done
