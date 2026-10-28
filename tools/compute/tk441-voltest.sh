#!/bin/bash
# TK-441 круг 3: numba-ядро MAD в tk439-vol.py на FONE_USDT (530 событий): md5 строк и стена; базовый / fm=0 / fm=1. Выход /data/tk0441/voltest/res.txt.
#   python3 /data/sched/alsched.py submit --kind work --cores 1 --max-runtime 10m --name tk441-voltest -- bash /data/tk0441/tk441-voltest.sh
O=/data/tk0441/voltest; mkdir -p $O; rm -f $O/done $O/res.txt
cd /data/tk0435 || exit 9
export NUMBA_CACHE_DIR=/data/tk0441/nbcache NOPROF=1 VOLDIR=/data/tk0441/vol
A="/data/tk0441/e2e/ev/windows-2026-09.csv /data/tk0441/e2e/xq.json gate-futures_usdt FONE_USDT --cube /data/tk0436/cube3 --cut 29759040"
PY=/data/tk0439/venv/bin/python
echo "base $(TK441_NUMBA=0 $PY /data/tk0441/tk441-volprof.py $A 2>$O/e0)" >> $O/res.txt
echo "fm0 $(TK441_NUMBA=1 TK441_FM=0 $PY /data/tk0441/tk441-volprof.py $A 2>$O/e1)" >> $O/res.txt
echo "fm1 $(TK441_NUMBA=1 TK441_FM=1 $PY /data/tk0441/tk441-volprof.py $A 2>$O/e2)" >> $O/res.txt
echo 0 > $O/done
