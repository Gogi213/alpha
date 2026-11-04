#!/bin/bash
# TK-490: закачка Bybit (свечи 1 м, затем OI) для доли Софии; только закачка и gzip, свой каталог (В-182)
p=$1; B=/opt/alpha-pump-dl; O=$B/out
export K_KEEPALIVE=1 K_GZIP=1 K_START=1735689600000 K_END=1793750400000
mkdir -p $O/candles $O/oi
python3 $B/bin/tk476-bybit-hist.py $O/candles $B/lists/sofia-cand-$p.txt 80 50
python3 $B/bin/tk490-oi.py bybit $O/oi $B/lists/sofia-oi-$p.txt 80 50
