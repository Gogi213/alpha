#!/bin/bash
cd /data/tk0436
R=/srv/alpha-sda/candles-1m-202607,/data/tk0436/julroot,/srv/alpha-sda/candles-1m-aug,/srv/alpha-sda/candles-1m
rm -f chk3.rc; : > chk3.log
rc=0
for a in "bybit-linear BTCUSDT" "bybit-linear DOGEUSDT" "bybit-spot BTCUSDT" "bybit-spot SOLUSDT" "gate-futures_usdt BTC_USDT" "gate-futures_usdt DOGE_USDT" "gate-spot BTC_USDT" "gate-spot DOGE_USDT"; do
  set -- $a
  /data/tk421/venv/bin/python3 tk436-cube.py check cube3 $1 $2 $R >> chk3.log 2>&1 || rc=1
done
echo $rc > chk3.rc
