#!/bin/bash
# TK-440 шаг 1: события v11 на янв–июнь 2026 тем же кодом и порогами (X_D = q99 сентября, xq.json без пересчёта). Метка ev.done, код ev.rc.
# L4/art для янв–июнь нет ("-": E3 не размечено, минуты артефактов не исключены) — оговорка в отчёте. Июль — после склейки свечей (три каталога).
#   alsched submit --kind prod --name tk440-ev --cores 15 --max-runtime 60m --est 40m -- bash /data/tk0440/tk440-events.sh
cd /data/tk0440 || exit 9
PY=/data/tk421/venv/bin/python3
rm -f ev.done ev.rc
for M in 01 02 03 04 05 06; do
  $PY /data/tk0433/v11/tk435-windows.py events /srv/alpha-sda/candles-1m-2026$M - - ev$M --procs 15 --ym 2026-$M --x q99 --xq /data/tk0433/v11/xq.json > ev$M.log 2>&1 || { echo "$M" > ev.rc; exit 1; }
done
echo 0 > ev.rc; touch ev.done
