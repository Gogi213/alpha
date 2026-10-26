#!/bin/bash
# TK-410 rest4: контроль-2 (дрейф монеты-дня) + перестановка знака на 14 монетах (out6, out-sui) и сводка В2/В3/В5. Метка rest4.done. cwd /data/tk0393/tk410
export PYTHONUTF8=1 LC_ALL=C.UTF-8
cd /data/tk0393/tk410 || exit 1; rm -f rest4.done; mkdir -p out7
PY=/data/tk0380/npvenv/bin/python
for c in BTC ETH LINK OP XAUT KGEN VVV; do [ -s mid-r2/$c.csv ] && echo "$c mid-r2/$c.csv"; done > r4-list.txt
while read c p; do echo "$c mid-sui/sui__$p.csv"; done < sui-list.txt >> r4-list.txt
xargs -P "${ALSCHED_CORES:-15}" -L1 sh -c 'GEV=/data/tk0393/tk401/gev2all '$PY' tk410-map3.py $0 $1 out7/$0 > out7/$0.log 2>&1' < r4-list.txt
$PY tk410-sum3.py out7 out6 out-sui > sum3.txt 2>&1
echo rc=$? > rest4.done
