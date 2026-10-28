#!/bin/bash
# TK-436: куб свечей сентября с запасом 37 суток (контроли ±30 + норма 7); июль = 202607 (Bybit 01–23) + tk425 (Bybit 24–31, Gate весь июль), август, сентябрь
cd /data/tk0436
R=/data/tk0436/julroot
mkdir -p $R
ln -sfn /srv/alpha-sda/tk425/jul/candles/bybit-linear $R/bybit-linear
ln -sfn /srv/alpha-sda/tk425/jul/candles/bybit-spot $R/bybit-spot
ln -sfn /srv/alpha-sda/tk425/jul-gate/candles/fut $R/gate-futures_usdt
ln -sfn /srv/alpha-sda/tk425/jul-gate/candles/spot $R/gate-spot
rm -f cube3.rc
CUBE_PAD=37 NPROC=15 /data/tk421/venv/bin/python3 tk436-cube.py candles 2026-09 /srv/alpha-sda/candles-1m cube3 /srv/alpha-sda/candles-1m-202607,$R,/srv/alpha-sda/candles-1m-aug > cube3.log 2>&1
echo $? > cube3.rc
