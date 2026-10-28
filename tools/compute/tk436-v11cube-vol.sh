#!/bin/bash
# TK-436 (возврат Судьи п.3-4): вол-меры v11 сентября на кубе с --cut = 1 авг. 00:00 UTC (история как у прежнего прогона: корни авг.+сент., без июля 24-31);
# побайтно против /data/tk0433/v11/vol-D*. Окна/квантили/события уже IDENTICAL (v11cube/cmp.txt).
cd /data/tk0435 || exit 9
PY=/data/tk421/venv/bin/python3
C=/data/tk0436/cube3
O=/data/tk0436/v11cube2
OLD=/data/tk0433/v11
CUT=29759040
rm -rf $O; mkdir -p $O; rm -f /data/tk0436/v11cube2.rc
T0=$(date +%s.%N)
for D in 90 180 360 720 1440; do
  for S in + -; do
    X=$($PY -c "import json;print(json.load(open('/data/tk0436/v11cube/xq.json'))['$D|$S']['q99'])")
    N=$([ "$S" = "+" ] && echo p || echo m)
    $PY tk435-vol.py /data/tk0436/v11cube/ev/windows-2026-09.csv $D $X $O/vol-D$D$N.csv --cube $C --cut $CUT --procs 15 > $O/vol-D$D$N.log 2>&1
  done
done
echo "вол-меры D×знак на кубе: $(echo "$(date +%s.%N) - $T0" | bc) s" > $O/time.txt
ok=1
for f in $OLD/vol-D*[pm].csv; do
  b=$(basename $f)
  cmp $f $O/$b > /dev/null 2>&1 && echo "OK $b" >> $O/cmp.txt || { echo "DIFF $b" >> $O/cmp.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/cmp.txt || echo DIFF >> $O/cmp.txt
echo 0 > /data/tk0436/v11cube2.rc
