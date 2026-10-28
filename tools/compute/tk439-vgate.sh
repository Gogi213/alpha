#!/bin/bash
# TK-439: гейт tk439-vol.py (пакетный расчёт мер) — 10 вол-файлов сентября на кубе (как tk436-v11cube-vol.sh), побайтно против /data/tk0433/v11/vol-D*.
#   alsched submit --kind work --name tk439-vgate --cores 15 --max-runtime 15m -- bash /data/tk0439/tk439-vgate.sh
cd /data/tk0435 || exit 9
PY=/data/tk421/venv/bin/python3
C=/data/tk0436/cube3
O=/data/tk0439/volnb
OLD=/data/tk0433/v11
CUT=29759040
rm -rf $O; mkdir -p $O; rm -f /data/tk0439/vg.rc
T0=$(date +%s.%N)
for D in 90 180 360 720 1440; do
  for S in + -; do
    X=$($PY -c "import json;print(json.load(open('/data/tk0436/v11cube/xq.json'))['$D|$S']['q99'])")
    N=$([ "$S" = "+" ] && echo p || echo m)
    $PY /data/tk0435/tk439-vol.py /data/tk0436/v11cube/ev/windows-2026-09.csv $D $X $O/vol-D$D$N.csv --cube $C --cut $CUT --procs 15 > $O/vol-D$D$N.log 2>&1
  done
done
echo "вол-меры D×знак (tk439-vol): $(echo "$(date +%s.%N) - $T0" | bc) s" > $O/time.txt
ok=1
for f in $OLD/vol-D*[pm].csv; do
  b=$(basename $f)
  cmp $f $O/$b > /dev/null 2>&1 && echo "OK $b" >> $O/cmp.txt || { echo "DIFF $b" >> $O/cmp.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/cmp.txt || echo DIFF >> $O/cmp.txt
echo 0 > /data/tk0439/vg.rc
