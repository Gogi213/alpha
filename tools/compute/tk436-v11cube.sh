#!/bin/bash
# TK-436 (возврат Судьи п.1, п.4): сквозная итерация v11 сентября (окна X=5 → квантили → события q99 → вол. 5 D × 2 знака) на КУБЕ cube3, 15 процессов;
# стена по шагам в t.tsv; выходы побайтно против прежнего прогона /data/tk0433/v11 (13 мин 32 с по меткам файлов: h 3:12, ev 1:21, vol 8:59).
cd /data/tk0435 || exit 9
PY=/data/tk421/venv/bin/python3
R=/srv/alpha-sda/candles-1m
L4=/data/tk421/l4v8/l4v8-2026-09.json
ART=/data/tk421/art-2026-09.json
C=/data/tk0436/cube3
O=/data/tk0436/v11cube
OLD=/data/tk0433/v11
rm -rf $O; mkdir -p $O/h $O/ev; rm -f /data/tk0436/v11cube.rc
T0=$(date +%s.%N); t() { echo -e "$1\t$(echo "$(date +%s.%N) - $T0" | bc)" >> $O/t.tsv; }
$PY tk435-windows.py events $R $L4 $ART $O/h --procs 15 --x 5 --cube $C > $O/h.log 2>&1; t h
$PY tk435-windows.py quant $O/h/hist-2026-09.json $O/xq.json > $O/quant.log 2>&1; t quant
$PY tk435-windows.py events $R $L4 $ART $O/ev --procs 15 --x q99 --xq $O/xq.json --cube $C > $O/ev.log 2>&1; t ev
for D in 90 180 360 720 1440; do
  for S in + -; do
    X=$($PY -c "import json;print(json.load(open('$O/xq.json'))['$D|$S']['q99'])")
    N=$([ "$S" = "+" ] && echo p || echo m)
    $PY tk435-vol.py $O/ev/windows-2026-09.csv $D $X $O/vol-D$D$N.csv --cube $C --procs 15 > $O/vol-D$D$N.log 2>&1; t vol-D$D$N
  done
done
ok=1
for f in h/windows-2026-09.csv h/hist-2026-09.json h/funnel-2026-09.json xq.json ev/windows-2026-09.csv ev/funnel-2026-09.json vol-D*[pm].csv; do
  cmp $OLD/$f $O/$f > /dev/null 2>&1 && echo "OK $f" >> $O/cmp.txt || { echo "DIFF $f" >> $O/cmp.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/cmp.txt || echo DIFF >> $O/cmp.txt
echo 0 > /data/tk0436/v11cube.rc
