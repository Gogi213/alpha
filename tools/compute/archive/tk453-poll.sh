#!/usr/bin/env bash
# TK-453: ждёт дневных свечей Binance 28.10+ (BTCUSDT), затем подаёт в alsched net тк453-pipe.sh. Итог — /data/tk453/done. usage: tk453-poll.sh <deadline_epoch>
set -u
D=/data/tk453
U=https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1m/BTCUSDT-1m
have() { [ "$(curl -s -o /dev/null -w '%{http_code}' -I "$U-$1.zip")" = 200 ]; }
until have 2026-10-28; do
  if [ "$(date +%s)" -ge "$1" ]; then echo "404: BTCUSDT-1m-2026-10-28 не опубликован к $(date -u +%F\ %T) UTC" > $D/done; exit 0; fi
  sleep 900
done
t=$(( $(date +%s) + 5400 ))   # 28.10 вышел — ещё до 90 мин на 29.10
until have 2026-10-29 || [ "$(date +%s)" -ge "$t" ]; do sleep 600; done
python3 /data/sched/alsched.py submit --cls net --kind net --name tk453-pipe --max-runtime 2h --cores 1 --mem 8 --disk none --cwd $D -- bash $D/tk453-pipe.sh
