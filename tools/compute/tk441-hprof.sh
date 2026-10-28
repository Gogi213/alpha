#!/bin/bash
# TK-441 круг 3: шаг h (tk439-windows events --x 5, сент., 15 процессов) с метками времени этапов (TK441_T=1): пул до последнего рынка vs хвост главного (E3, сортировка, пары, DictWriter, funnel). Два прогона подряд.
#   python3 /data/sched/alsched.py submit --kind work --name tk441-hprof --cores 15 --mem 40 --max-runtime 10m --est 2m -- bash /data/tk0441/tk441-hprof.sh
cd /data/tk0435 || exit 9
cp /data/tk0441/tk441-hwin.py /data/tk0435/tk441-hwin.py || exit 8   # копия tk439-windows.py с TK441_T; боевой файл не трогаем
NB=/data/tk0439/venv/bin/python3
export NUMBA_CACHE_DIR=/data/tk0439/nbcache TK441_T=1
R=/srv/alpha-sda/candles-1m; L4=/data/tk421/l4v8/l4v8-2026-09.json; ART=/data/tk421/art-2026-09.json; C=/data/tk0436/cube3
O=/data/tk0441/hprof2
rm -rf $O; mkdir -p $O
for i in 1 2; do
  mkdir -p $O/h$i; T0=$(date +%s.%N)
  $NB tk441-hwin.py events $R $L4 $ART $O/h$i --procs 15 --x 5 --cube $C > $O/h$i.log 2> $O/h$i.err
  echo "wall$i $(echo "$(date +%s.%N) - $T0" | bc)" >> $O/res.txt
  cmp /data/tk0441/e2e/h/windows-2026-09.csv $O/h$i/windows-2026-09.csv && echo "IDENTICAL$i" >> $O/res.txt
  tail -n 12 $O/h$i.err >> $O/res.txt
done
echo 0 > $O/rc
