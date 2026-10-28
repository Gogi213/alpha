#!/bin/bash
# TK-440 шаг 2: куб свечей «инструмент × минута» на каждый месяц янв–июль 2026 (tk436-cube.py candles, запас 37 сут из предыдущего месяца).
# Июль = 202607 (Bybit 01–23) + julroot (tk425: Bybit 24–31, Gate весь июль) + июнь. Янв: декабря 2025 нет → NaN в запасе (оговорка).
#   alsched submit --kind prod --name tk440-cube --cores 15 --max-runtime 60m --est 30m -- bash /data/tk0440/tk440-cube.sh
cd /data/tk0436 || exit 9
PY=/data/tk421/venv/bin/python3
R=/srv/alpha-sda/candles-1m
cd /data/tk0440; rm -f cube.done cube.rc; cd /data/tk0436
for M in 01 02 03 04 05 06 07; do
  P=$(printf "%02d" $((10#$M - 1)))
  EX=/srv/alpha-sda/candles-1m-2026$P
  [ "$M" = 01 ] && EX=-
  [ "$M" = 07 ] && EX=/srv/alpha-sda/candles-1m-202607,/data/tk0436/julroot,/srv/alpha-sda/candles-1m-202606
  CUBE_PAD=37 NPROC=15 $PY tk436-cube.py candles 2026-$M $R /data/tk0440/cube$M $EX > /data/tk0440/cube$M.log 2>&1 || { echo $M > /data/tk0440/cube.rc; exit 1; }
done
echo 0 > /data/tk0440/cube.rc; touch /data/tk0440/cube.done
