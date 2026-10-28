#!/bin/bash
# TK-436: куб OI/фандинга/ликвидаций (сетка 5 мин) под куб свечей cube3 (сентябрь, запас 37 сут) + сверка с независимым разбором
cd /data/tk0436
O=/srv/alpha-sda/tk415/out
rm -f oi3.rc; : > oi3.log
PY=/data/tk421/venv/bin/python3
CUBE_PAD=37 NPROC=4 $PY tk436-cube.py oi 2026-09 $O cube3 >> oi3.log 2>&1 || { echo 1 > oi3.rc; exit 1; }
rc=0
for a in "bybit-linear BTCUSDT" "bybit-linear DOGEUSDT" "bybit-linear SOLUSDT" "gate-futures_usdt BTC_USDT" "gate-futures_usdt DOGE_USDT" "gate-futures_usdt SOL_USDT"; do
  set -- $a
  $PY tk436-cube.py checkoi cube3 $1 $2 $O >> oi3.log 2>&1 || rc=1
done
echo $rc > oi3.rc
