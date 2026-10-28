#!/bin/bash
# TK-441: сквозной v11 + v11f сентября, шаг tk422 = /data/tk0441/new.py (шаги 1а–3); остальное как tk439-e2e.sh. Запускать prod на свободном calc.
#   v11: tk439-windows (numba) X=5 -> quant -> tk439-windows X=q99 -> tk439-volall (10 вол-файлов одним пулом); v11f: контроли -> tk422-features-seq (seq-кэш вида) -> merge.
#   python3 /data/sched/alsched.py submit --kind work --name tk441-e2e --cores 15 --max-runtime 30m --est 4m -- bash /data/tk0441/tk441-e2e.sh
cd /data/tk0435 || exit 9
PY=/data/tk421/venv/bin/python3
NB=/data/tk0439/venv/bin/python3
export NUMBA_CACHE_DIR=/data/tk0439/nbcache
R=/srv/alpha-sda/candles-1m; L4=/data/tk421/l4v8/l4v8-2026-09.json; ART=/data/tk421/art-2026-09.json; C=/data/tk0436/cube3
O=/data/tk0441/e2e
rm -rf $O; mkdir -p $O/h $O/ev $O/f; rm -f /data/tk0441/e2e.rc
T0=$(date +%s.%N); t() { echo -e "$1\t$(echo "$(date +%s.%N) - $T0" | bc)" >> $O/t.tsv; }
$NB tk439-windows.py events $R $L4 $ART $O/h --procs 15 --x 5 --cube $C > $O/h.log 2>&1 || { echo 1 > /data/tk0441/e2e.rc; exit 1; }; t h
$PY tk435-windows.py quant $O/h/hist-2026-09.json $O/xq.json > $O/quant.log 2>&1; t quant
$NB tk439-windows.py events $R $L4 $ART $O/ev --procs 15 --x q99 --xq $O/xq.json --cube $C > $O/ev.log 2>&1 || { echo 2 > /data/tk0441/e2e.rc; exit 2; }; t ev
$PY tk439-volall.py $O/ev/windows-2026-09.csv $O/xq.json $O --cube $C --cut 29759040 --procs 15 > $O/volall.log 2>&1 || { echo 3 > /data/tk0441/e2e.rc; exit 3; }; t vol
cd /data/tk0433/v11f || exit 9
$PY /data/tk0436/tk433-features.py windows smp/legs-2026-09.csv smp/legs-2026-09.csv - - $O/f 2026-09 --cube $C --cut 29759040 --jobs 15 > $O/windows.log 2>&1 || { echo 4 > /data/tk0441/e2e.rc; exit 4; }; t controls
$PY /data/tk0441/new.py $O/f/windows.csv $O/f/tk422-features.csv --procs 15 --aug-candles /srv/alpha-sda/candles-1m-aug --instr /srv/alpha-sda/tk423/cov/bybit-instruments.tsv --cube $C --cube-trades /data/tk0436/cube3t --cube-from 2026-08-01T00:00 > $O/feat.log 2> $O/feat.err || { echo 5 > /data/tk0441/e2e.rc; exit 5; }; t tk422
$PY /data/tk0436/tk433-features.py merge $O/f/windows.csv $O/f/own.csv $O/f/tk422-features.csv $O/f/features-2026-09.csv > $O/merge.log 2>&1 || { echo 6 > /data/tk0441/e2e.rc; exit 6; }; t merge
ok=1; OLD=/data/tk0433/v11
for f in h/windows-2026-09.csv h/hist-2026-09.json h/funnel-2026-09.json xq.json ev/windows-2026-09.csv ev/funnel-2026-09.json; do
  cmp $OLD/$f $O/$f > /dev/null 2>&1 && echo "OK $f" >> $O/cmp.txt || { echo "DIFF $f" >> $O/cmp.txt; ok=0; }
done
for f in $OLD/vol-D*[pm].csv; do b=$(basename $f); cmp $f $O/$b > /dev/null 2>&1 && echo "OK $b" >> $O/cmp.txt || { echo "DIFF $b" >> $O/cmp.txt; ok=0; }; done
for f in windows.csv own.csv controls-diag.json tk422-features.csv; do
  cmp /data/tk0433/v11f/f-2026-09/$f $O/f/$f > /dev/null 2>&1 && echo "OK $f" >> $O/cmp.txt || { echo "DIFF $f" >> $O/cmp.txt; ok=0; }
done
cmp /data/tk0433/v11f/g-2026-09/features-2026-09.csv $O/f/features-2026-09.csv > /dev/null 2>&1 && echo "OK features-2026-09.csv" >> $O/cmp.txt || { echo "DIFF features-2026-09.csv" >> $O/cmp.txt; ok=0; }
[ $ok = 1 ] && echo IDENTICAL >> $O/cmp.txt || echo DIFF >> $O/cmp.txt
echo 0 > /data/tk0441/e2e.rc
