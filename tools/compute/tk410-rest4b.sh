#!/bin/bash
# TK-410 rest4b: Sui 7 монет (контроль-2 + перестановка) + сводка sum3 по всем 14 (после rest4). Метка rest4b.done. cwd /data/tk0393/tk410
export PYTHONUTF8=1 LC_ALL=C.UTF-8
cd /data/tk0393/tk410 || exit 1; rm -f rest4b.done; mkdir -p out7
PY=/data/tk0380/npvenv/bin/python
while read c p; do echo "$c mid-sui/sui__$p.csv"; done < sui-list.txt | xargs -P "${ALSCHED_CORES:-15}" -L1 sh -c 'GEV=/data/tk0393/tk401/gev2all '$PY' tk410-map3.py $0 $1 out7/$0 > out7/$0.log 2>&1'
rm -f out7/mid-*; $PY tk410-sum3.py out7 out6 out-sui > sum3.txt 2>&1
echo rc=$? > rest4b.done
