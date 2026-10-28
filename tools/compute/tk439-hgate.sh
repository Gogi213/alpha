#!/bin/bash
# TK-439: гейт детектора окон (numba-ядро) — events X=5 на кубе, сентябрь, 15 процессов; сверка побайтно с /data/tk0436/v11cube/h (эталон TK-436).
cd /data/tk0435 || exit 9
O=${O:-/data/tk0439/hnb}
rm -rf $O; mkdir -p $O; rm -f /data/tk0439/hg.rc
export NUMBA_CACHE_DIR=/data/tk0439/nbcache
S=$(date +%s.%N)
/data/tk0439/venv/bin/python3 /data/tk0435/tk439-windows.py events /srv/alpha-sda/candles-1m /data/tk421/l4v8/l4v8-2026-09.json /data/tk421/art-2026-09.json $O --procs 15 --x 5 --cube /data/tk0436/cube3 > $O.log 2>&1 || { echo 1 > /data/tk0439/hg.rc; exit 1; }
echo "wall $(echo "$(date +%s.%N) - $S" | bc)" > $O.time
ok=1
for f in windows-2026-09.csv hist-2026-09.json funnel-2026-09.json; do
  cmp /data/tk0436/v11cube/h/$f $O/$f > /dev/null 2>&1 && echo "OK $f" >> $O.time || { echo "DIFF $f" >> $O.time; ok=0; }
done
[ $ok = 1 ] && echo IDENTICAL >> $O.time || echo DIFF >> $O.time
echo 0 > /data/tk0439/hg.rc
