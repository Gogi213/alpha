#!/bin/bash
# TK-441 круг 3: профиль вол-задания FONE_USDT (530 событий), 1 ядро. Выход /data/tk0441/volprof/prof.txt.
#   python3 /data/sched/alsched.py submit --kind work --cores 1 --max-runtime 10m --name tk441-volprof -- bash /data/tk0441/tk441-volprof.sh
O=/data/tk0441/volprof; mkdir -p $O; rm -f $O/done
cd /data/tk0435 || exit 9
/data/tk421/venv/bin/python3 /data/tk0441/tk441-volprof.py /data/tk0441/e2e/ev/windows-2026-09.csv /data/tk0441/e2e/xq.json gate-futures_usdt FONE_USDT --cube /data/tk0436/cube3 --cut 29759040 > $O/prof.txt 2> $O/prof.err
echo $? > $O/done
