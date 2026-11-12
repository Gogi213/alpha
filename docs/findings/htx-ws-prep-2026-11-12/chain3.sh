#!/bin/bash
# Цепочка 3 HTX (srv): X3/X4 — ещё две пачки по 5 соединений на api.hbdm.com/linear-swap-ws (топ-10 bbo), 50 с, с замером TCP connect и ss -ti на 20 с: связь «медленного» соединения (лаг растёт) с rtt TCP.
cd /data/htx-probe
CAP="nice -n -10 /root/bnws-venv/bin/python g022-prep-3-tools/cap.py --gz --htx-pong --tcp-probe"
for b in 3 4; do
  echo "X$b $(date -u +%T)"
  for k in A B C D E; do $CAP --out frames/X$b-$k-bbo10.jsonl.zst --secs 50 --url wss://api.hbdm.com/linear-swap-ws --msgs-file subs/X-bbo10.json --sub-pause 0.03 > frames/X$b-$k.log 2>&1 & done
  sleep 22; ss -tin dst 43.169.95.96 > frames/X$b-ss-22s.txt 2>&1
  wait
done
echo "done $(date -u +%T)"
touch chain3.done
