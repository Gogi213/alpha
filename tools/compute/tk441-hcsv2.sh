#!/bin/bash
# TK-441 круг 3 (возврат Судьи 03:29, п.2): шаг h/ev — форматирование строк CSV чанками в воркерах (TK441_CSVP=1), метки этапов (TK441_M=1); cmp с эталонами e2e. Кэш куба прогрет tk441-hcsv.sh.
#   python3 /data/sched/alsched.py submit --kind work --name tk441-hcsv3 --cores 15 --mem 40 --max-runtime 12m --est 2m -- bash /data/tk0441/tk441-hcsv2.sh
D=/data/tk0441/hcsv; cd $D || exit 9; cp $D/tk435-windows.new $D/tk435-windows.py
NB=/data/tk0439/venv/bin/python3
export NUMBA_CACHE_DIR=/data/tk0439/nbcache TK441_M=1 TK441_CSVP=1
R=/srv/alpha-sda/candles-1m; L4=/data/tk421/l4v8/l4v8-2026-09.json; ART=/data/tk421/art-2026-09.json; C=/data/tk0436/cube3
E=/data/tk0441/e2e; O=$D/outp; rm -rf $O; mkdir -p $O
run() {
  n=$1; shift; mkdir -p $O/$n; T0=$(date +%s.%N)
  $NB $D/tk439-windows.py events $R $L4 $ART $O/$n --procs 15 --cube $C "$@" > $O/$n.log 2> $O/$n.err
  echo "$n wall $(echo "$(date +%s.%N) - $T0" | bc)" >> $O/res.txt; grep '^M ' $O/$n.err | tr '\n' ' ' >> $O/res.txt; echo >> $O/res.txt
}
run h4 --x 5
run ev --x q99 --xq $E/xq.json
cmp $E/h/windows-2026-09.csv $O/h4/windows-2026-09.csv && cmp $E/h/funnel-2026-09.json $O/h4/funnel-2026-09.json && cmp $E/h/hist-2026-09.json $O/h4/hist-2026-09.json && echo "IDENTICAL h4" >> $O/res.txt || echo "DIFF h4" >> $O/res.txt
cmp $E/ev/windows-2026-09.csv $O/ev/windows-2026-09.csv && cmp $E/ev/funnel-2026-09.json $O/ev/funnel-2026-09.json && echo "IDENTICAL ev" >> $O/res.txt || echo "DIFF ev" >> $O/res.txt
echo 0 > $O/rc
