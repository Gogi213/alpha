#!/bin/bash
# TK-441 круг 3: вол-шаг сентября с numba-ядром MAD (TK441_NUMBA=1, tk439-vol.py из /data/tk0441/vol), 15 процессов, входы прогона 2; cmp 10 файлов с принятыми /data/tk0433/v11.
#   python3 /data/sched/alsched.py submit --kind work --cores 15 --mem 40 --max-runtime 15m --name tk441-volgate -- bash /data/tk0441/tk441-volgate.sh
O=/data/tk0441/volgate; rm -rf $O; mkdir -p $O
cd /data/tk0441/vol || exit 9
export NUMBA_CACHE_DIR=/data/tk0441/nbcache TK441_NUMBA=1 TK441_FM=0
E=/data/tk0441/e2e
s=$(date +%s.%N)
/data/tk0439/venv/bin/python tk439-volall.py $E/ev/windows-2026-09.csv $E/xq.json $O --cube /data/tk0436/cube3 --cut 29759040 --procs 15 > $O/volall.log 2> $O/volall.err
echo "vol $(echo "$(date +%s.%N) - $s" | bc)" > $O/res.txt
ok=1; for f in /data/tk0433/v11/vol-D*[pm].csv; do b=$(basename $f); cmp $f $O/$b >/dev/null 2>&1 && echo "OK $b" >> $O/res.txt || { echo "DIFF $b" >> $O/res.txt; ok=0; }; done
[ $ok = 1 ] && echo IDENTICAL >> $O/res.txt || echo DIFF >> $O/res.txt
echo 0 > $O/done
