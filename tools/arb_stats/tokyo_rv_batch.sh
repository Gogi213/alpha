#!/bin/bash
# Проверяющий arb-tokyo: три юнита systemd-run (rv-batch, rv-batch3, rv-batch2), склеены сюда для истории. На srv скрипты лежат под короткими именами: bybit_cts.py, clock2.py, tokyo_leg.py, taint.py (здесь — tokyo_rv_*.py).
# проверяющий arb-tokyo: прогоны на 48 монетах выборки сдвига; книги Bybit по ts (как отчёт) и по cts (движок)
set -x
cd /opt/arb-tokyo-review/tools/arb_stats
O=/data/arb-tokyo-review
read T0 T1 < <(python3 -c "import json;d=json.load(open('/data/arb-tokyo/window.json'));print(d['t0'],d['t1'])")
C=$(cat /data/arb-tokyo/shift_coins.txt)
export DEAD=/data/arb-tokyo/full/dead.json
python3 clock2.py /data/arb-tokyo/full/w $T0 $T1 $O/clock2.json bts:$O/bts bcts:$O/bcts
for k in bts bcts; do
  COINS=$C MODES=life,tt python3 tokyo_run.py $O/$k /data/arb-tokyo/full/w $T0 $T1 $O/run-$k.pkl 3
  COINS=$C python3 tokyo_leg.py $O/$k $T0 $T1 $O/leg-$k.pkl 3
done
for k in bts bcts; do
  COINS=$C MODES=mk python3 tokyo_run.py $O/$k /data/arb-tokyo/full/w $T0 $T1 $O/mk-$k.pkl 3
done
echo BATCH_DONE
set -x
cd /opt/arb-tokyo-review/tools/arb_stats
O=/data/arb-tokyo-review
read T0 T1 < <(python3 -c "import json;d=json.load(open('/data/arb-tokyo/window.json'));print(d['t0'],d['t1'])")
export DEAD=/data/arb-tokyo/full/dead.json
C=$(cat /data/arb-tokyo/shift_coins.txt)
COINS=$C EXSHIFT=bybit:-9,okx:-6 python3 tokyo_leg.py $O/bts $T0 $T1 $O/leg-eng.pkl 2
echo BATCH3_DONE
set -x
cd /opt/arb-tokyo-review/tools/arb_stats
O=/data/arb-tokyo-review
read T0 T1 < <(python3 -c "import json;d=json.load(open('/data/arb-tokyo/window.json'));print(d['t0'],d['t1'])")
python3 funding_fetch.py $T0 $T1 $O/funding.json
DEAD=/data/arb-tokyo/full/dead.json AUDIT=/data/collect-gap/out FUNDING=$O/funding.json python3 taint.py /data/arb-tokyo/full/b $T0 $T1 $O/taint.pkl 2
echo BATCH2_DONE
