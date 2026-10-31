#!/usr/bin/env bash
# TK-453: ждёт final3 (докачка 29/30.10), затем опрашивает 28.10 (BTCUSDT) до $1 (epoch); вышел -> pipe4, нет -> итог 404. Итог — /data/tk453/final4.
set -u
D=/data/tk453
U=https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m
have() { [ "$(curl -s -o /dev/null -w "%{http_code}" -I "$U-$1.zip")" = 200 ]; }
until [ -f $D/final3 ]; do sleep 60; done
until have 2026-10-28; do
  if [ "$(date +%s)" -ge "$1" ]; then echo "404: BTCUSDT-1m-2026-10-28 не опубликован к $(date -u +%F\ %T) UTC" > $D/final4; exit 0; fi
  sleep 900
done
python3 /data/sched/alsched.py submit --kind net --cores 1 --max-runtime 7200 --est 1800 --mem 8 --disk none --name tk453-pipe4 --cwd $D -- bash $D/tk453-pipe.sh $D/final4
