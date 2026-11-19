#!/bin/bash
# $1 kind $2 group $3 repeat [$4 exch=binance]; ступени ($STEPS) до первого излома: обрыв, устойчивый лаг (lag_p50 > 2000 мс) или тишина дольше половины окна.
# Готовый результат ступени не пересчитывается (В-213). Сырьё — /data/<bnws|bbws>-ladder, по файлу на ступень: <kind>-<group>-n<n>-r<повтор>.json
X=${4:-binance}; P=bnws; [ $X = bybit ] && P=bbws
D=/data/$P-ladder; mkdir -p $D
H=$(cd "$(dirname "$0")" && pwd); PY=${PY:-/root/bnws-venv/bin/python}
for n in ${STEPS:-10 20 40 80 160 320}; do
  f=$D/$1-$2-n$n-r$3.json
  [ -s $f ] || $PY $H/ladder.py $1 $n $2 180 $f $X
  python3 - "$f" <<'PY' || exit 0
import json,sys
r=json.load(open(sys.argv[1]))
bad = r.get("error") or r["disconnects"] or (r.get("lag_p50") or 0)>2000 or r.get("silent_s",0)>r["dur"]/2
sys.exit(1 if bad else 0)
PY
  sleep ${PAUSE:-20}
done
