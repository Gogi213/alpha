#!/bin/bash
# TK-440 шаг 3: события v11 июль 2026. Склейка свечей: июнь + 202607 (01–23) + julroot (tk425: 24–31) по файлам инструмента, заголовок один.
# Gate июль целиком из julroot; июнь — запас окна (D до 1440 мин). Метка ev07.done, код ev07.rc.
#   alsched submit --kind prod --name tk440-ev07 --cores 15 --max-runtime 40m --est 20m -- bash /data/tk0440/tk440-events07.sh
cd /data/tk0440 || exit 9
PY=/data/tk421/venv/bin/python3
rm -f ev07.done ev07.rc; rm -rf jm; mkdir jm
for D in bybit-linear bybit-spot gate-futures_usdt gate-spot; do
  mkdir -p jm/$D
  ls /srv/alpha-sda/candles-1m-202606/$D /srv/alpha-sda/candles-1m-202607/$D /data/tk0436/julroot/$D 2>/dev/null | grep csv.gz | sort -u > jm/$D.lst
  export D
  xargs -P 15 -I{} bash -c 'f={}; { head -c0 /dev/null; for R in /srv/alpha-sda/candles-1m-202606 /srv/alpha-sda/candles-1m-202607 /data/tk0436/julroot; do [ -f $R/$D/$f ] && zcat $R/$D/$f; done | awk "NR==1||!/^t_open_ms/" | gzip -1 > jm/$D/$f; }' < jm/$D.lst
done
$PY /data/tk0433/v11/tk435-windows.py events /data/tk0440/jm - - ev07 --procs 15 --ym 2026-07 --x q99 --xq /data/tk0433/v11/xq.json > ev07.log 2>&1 || { echo 1 > ev07.rc; exit 1; }
echo 0 > ev07.rc; touch ev07.done
