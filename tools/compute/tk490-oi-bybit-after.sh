#!/bin/bash
# TK-490: OI Bybit стартует, когда свечи Bybit закончили (общий лимит IP ~100 запр/с): две половины списка по 50 запр/с
H=$HOME/alpha-pump
while systemctl --user is-active -q pump-candles-bybit-a || systemctl --user is-active -q pump-candles-bybit-b; do sleep 30; done
for p in a b; do
  python3 $H/bin/tk490-oi.py bybit $H/oi $H/universe/symbols-bybit-linear-$p.txt 80 50 > $H/log/pump-oi-bybit-$p.log 2>&1 &
done
wait
