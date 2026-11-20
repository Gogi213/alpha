#!/bin/bash
# Цепочка 9 Bitget (srv): T1 — 90 с, 5 соединений: два v3 ticker (ключ дедупа между соединениями), v3 publicTrade top-100 против v2 trade top-100 (совпадение id), v2 books1 top-10 через ws.bitgetapi.com (второй хост).
cd /data/bitget-probe
while [ ! -e chain8.done ]; do sleep 2; done
CAP="nice -n -10 /root/bnws-venv/bin/python g022-prep-3-tools/cap.py --ping text"
P2="/root/bnws-venv/bin/python g022-prep-3-tools/plan.py bitget --pool pool-2026-11-12.json"
P3="/root/bnws-venv/bin/python g022-prep-3-tools/plan.py bitget3 --pool pool-2026-11-12.json"
$P3 --top 10 --kinds ticker --out subs/T1-ticker.json
$P3 --top 100 --kinds publicTrade --out subs/T1-ptrade.json
$P2 --top 100 --kinds trade --out subs/T1-trade.json
$P2 --top 10 --kinds books1 --out subs/T1-b10.json
echo "T1 $(date -u +%T)"
$CAP --out frames/T1-A-v3ticker.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/T1-ticker.json > frames/T1-A.log 2>&1 &
$CAP --out frames/T1-B-v3ticker.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/T1-ticker.json > frames/T1-B.log 2>&1 &
$CAP --out frames/T1-C-v3publictrade100.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/T1-ptrade.json > frames/T1-C.log 2>&1 &
$CAP --out frames/T1-D-v2trade100.jsonl.zst --secs 90 --url wss://ws.bitget.com/v2/ws/public --msgs-file subs/T1-trade.json > frames/T1-D.log 2>&1 &
$CAP --out frames/T1-E-bitgetapi-v2books1.jsonl.zst --secs 90 --url wss://ws.bitgetapi.com/v2/ws/public --msgs-file subs/T1-b10.json > frames/T1-E.log 2>&1 &
wait
echo "done $(date -u +%T)"
touch chain9.done
