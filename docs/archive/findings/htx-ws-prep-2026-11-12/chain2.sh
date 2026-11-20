#!/bin/bash
# Цепочка 2 HTX (srv): X1/X2 — по 5 соединений на api.hbdm.com/linear-swap-ws (топ-10 bbo), 60 с: сколько соединений «медленных» (лаг растёт) и что видно по ss -ti (rtt, потери); X1-A — mark_price.1min на ws_index.
cd /data/htx-probe
PY=/root/bnws-venv/bin/python
CAP="nice -n -10 $PY g022-prep-3-tools/cap.py --gz --htx-pong"
PLAN="$PY g022-prep-3-tools/plan.py htx --pool pool-2026-11-12.json"
$PLAN --top 10 --kinds bbo --out subs/X-bbo10.json
$PLAN --kinds mark_price.1min --out subs/X1-mark.json
for b in 1 2; do
  echo "X$b $(date -u +%T)"
  if [ $b = 1 ]; then $CAP --out frames/X1-A-index-markprice.jsonl.zst --secs 60 --url wss://api.hbdm.com/ws_index --msgs-file subs/X1-mark.json --sub-pause 0.03 > frames/X1-A.log 2>&1 & fi
  for k in B C D E F; do
    [ $b = 1 ] && [ $k = F ] && continue
    $CAP --out frames/X$b-$k-bbo10.jsonl.zst --secs 60 --url wss://api.hbdm.com/linear-swap-ws --msgs-file subs/X-bbo10.json --sub-pause 0.03 > frames/X$b-$k.log 2>&1 &
  done
  sleep 25; ss -tin dst 43.169.95.96 > frames/X$b-ss-25s.txt 2>&1
  sleep 25; ss -tin dst 43.169.95.96 > frames/X$b-ss-50s.txt 2>&1
  wait
done
echo "done $(date -u +%T)"
touch chain2.done
