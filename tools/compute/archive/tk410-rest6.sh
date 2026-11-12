#!/bin/bash
# TK-410 rest6: map4 (maxT Westfall-Young, K=2000, семья = все клетки монеты) на 14 монетах + синтетика (SYNTH, K=500) + сводка sum4. Метка rest6.done. cwd /data/tk0393/tk410
# подача: alsched submit --name tk410-rest6 --cores 15 --max-runtime 1h --est 25m --cwd /data/tk0393/tk410 -- bash /data/tk0393/tk410/tk410-rest6.sh
export PYTHONUTF8=1 LC_ALL=C.UTF-8
cd /data/tk0393/tk410 || exit 1; rm -f rest6.done; mkdir -p out9 outS2
PY=/data/tk0380/npvenv/bin/python
xargs -P "${ALSCHED_CORES:-15}" -L1 sh -c 'GEV=/data/tk0393/tk401/gev2all KPERM=2000 '$PY' tk410-map4.py $0 $1 out9/$0 > out9/$0.log 2>&1' < r5-list.txt
head -3 r5-list.txt | xargs -P 3 -L1 sh -c 'SYNTH=1 KPERM=500 GEV=/data/tk0393/tk401/gev2all '$PY' tk410-map4.py $0 $1 outS2/$0 > outS2/$0.log 2>&1'
$PY tk410-sum4.py out9 > sum4.txt 2>&1; rc=$?
$PY - > synth2.txt 2>&1 <<'PYEOF'
import glob, csv, numpy as np
p = [float(r['pw_exc']) for f in glob.glob('outS2/*-curve3.csv') for r in csv.DictReader(open(f)) if r.get('pw_exc') not in (None, '', 'nan')]
q = [float(r['pw_t']) for f in glob.glob('outS2/*-curve3.csv') for r in csv.DictReader(open(f)) if r.get('pw_t') not in (None, '', 'nan')]
print('synth cells', len(p), 'share pw_exc<0.05', np.mean(np.array(p) < .05) if p else None, 'share pw_t<0.05', np.mean(np.array(q) < .05) if q else None)
PYEOF
echo rc=$rc > rest6.done
