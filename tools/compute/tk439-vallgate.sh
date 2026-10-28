#!/bin/bash
# TK-439: гейт tk439-volall.py (10 вол-файлов одним пулом) — побайтно против /data/tk0433/v11/vol-D*.
#   alsched submit --kind work --name tk439-vallgate --cores 15 --max-runtime 15m -- bash /data/tk0439/tk439-vallgate.sh
cd /data/tk0435 || exit 9
PY=/data/tk421/venv/bin/python3
O=/data/tk0439/volall
OLD=/data/tk0433/v11
rm -rf $O; mkdir -p $O; rm -f /data/tk0439/vag.rc
T0=$(date +%s.%N)
$PY /data/tk0435/tk439-volall.py /data/tk0436/v11cube/ev/windows-2026-09.csv /data/tk0436/v11cube/xq.json $O --cube /data/tk0436/cube3 --cut 29759040 --procs 15 > $O/volall.log 2>&1
echo "вол-меры 10 файлов (tk439-volall): $(echo "$(date +%s.%N) - $T0" | bc) s" > $O/time.txt
ok=1
for f in $OLD/vol-D*[pm].csv; do
  b=$(basename $f)
  cmp $f $O/$b > /dev/null 2>&1 && echo "OK $b" >> $O/cmp.txt || { echo "DIFF $b" >> $O/cmp.txt; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O/cmp.txt || echo DIFF >> $O/cmp.txt
echo 0 > /data/tk0439/vag.rc
