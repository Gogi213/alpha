#!/bin/bash
# Цепочка 4 HTX (srv): F1 — пул (49), окно 180 с на «быстрых» соединениях (до 10 попыток на соединение: медленный маршрут — лаг > 3 с на 5–15 с — отбрасывается): bbo, trade.detail, mark_price.1min (ws_index), index.1min (ws_index), funding_rate (notification).
cd /data/htx-probe
F="bash g022-prep-3-tools/fast.sh"
C="--gz --htx-pong --sub-pause 0.03"
rm -f frames/F1-*.tries
echo "F1 $(date -u +%T)"
$F frames/F1-A-bbo.jsonl.zst 10 --secs 180 --url wss://api.hbdm.com/linear-swap-ws --msgs-file subs/H1-A.json $C > frames/F1-A.log 2>&1 &
$F frames/F1-B-trade.jsonl.zst 10 --secs 180 --url wss://api.hbdm.com/linear-swap-ws --msgs-file subs/H1-B.json $C > frames/F1-B.log 2>&1 &
$F frames/F1-C-mark.jsonl.zst 10 --secs 180 --url wss://api.hbdm.com/ws_index --msgs-file subs/X1-mark.json $C > frames/F1-C.log 2>&1 &
$F frames/F1-D-index.jsonl.zst 10 --secs 180 --url wss://api.hbdm.com/ws_index --msgs-file subs/H2-A.json $C > frames/F1-D.log 2>&1 &
$F frames/F1-E-funding.jsonl.zst 10 --secs 180 --url wss://api.hbdm.com/linear-swap-notification --msgs-file subs/H1-E.json $C > frames/F1-E.log 2>&1 &
wait
echo "done $(date -u +%T)"
touch chain4.done
