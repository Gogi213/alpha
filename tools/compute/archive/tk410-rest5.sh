#!/bin/bash
# TK-410 rest5: map3 с исправленной перестановкой (пул up+down внутри монета-дня, разбор Судьи 17:45) на 14 монетах + проверка на синтетике (SYNTH) + сводка. Метка rest5.done. cwd /data/tk0393/tk410
# подача: alsched submit --name tk410-rest5 --cores 15 --max-runtime 1h --est 25m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-rest5.sh
export PYTHONUTF8=1 LC_ALL=C.UTF-8
cd /data/tk0393/tk410 || exit 1; rm -f rest5.done; mkdir -p out8 outS
PY=/data/tk0380/npvenv/bin/python
for c in BTC ETH LINK OP XAUT KGEN VVV; do [ -s mid-r2/$c.csv ] && echo "$c mid-r2/$c.csv"; done > r5-list.txt
while read c p; do echo "$c mid-sui/sui__$p.csv"; done < sui-list.txt >> r5-list.txt
xargs -P "${ALSCHED_CORES:-15}" -L1 sh -c 'GEV=/data/tk0393/tk401/gev2all '$PY' tk410-map3.py $0 $1 out8/$0 > out8/$0.log 2>&1' < r5-list.txt
# проверка теста: случайные метки -> perm_p ~ U(0,1)
head -3 r5-list.txt | xargs -P 3 -L1 sh -c 'SYNTH=1 GEV=/data/tk0393/tk401/gev2all '$PY' tk410-map3.py $0 $1 outS/$0 > outS/$0.log 2>&1'
$PY tk410-sum3.py out8 > sum3.txt 2>&1; rc=$?
cp sum3-ctrl2.csv sum3-ctrl2-r5.csv; cp sum3-b235.csv sum3-b235-r5.csv
$PY - > synth.txt 2>&1 <<'PYEOF'
import glob, csv, numpy as np
p = [float(r['perm_p']) for f in glob.glob('outS/*-curve3.csv') for r in csv.DictReader(open(f)) if r['perm_p'] not in ('', 'nan')]
print('synth cells', len(p), 'mean p', np.mean(p) if p else None, 'share p<0.05', np.mean(np.array(p) < .05) if p else None)
r = [(float(x['ci3_lo']), float(x['perm_p']), f) for f in glob.glob('out8/*-curve3.csv') for x in csv.DictReader(open(f)) if x['ci3_lo'] not in ('', 'nan') and float(x['ci3_lo']) > 15]
print('real ci3_lo>15 cells', len(r), 'max perm_p', max([a[1] for a in r], default=None))
PYEOF
echo rc=$rc > rest5.done
