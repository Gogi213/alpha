#!/bin/bash
# Цепочка 4 Bitget (srv): R1 — повтор обрыва UTA v3 books1 на весь пул (399 на одном соединении) против двух половин (200+199) и v2 books1 399, 90 с, 4 соединения.
cd /data/bitget-probe
while [ ! -e chain3.done ]; do sleep 2; done
PY=/root/bnws-venv/bin/python
CAP="$PY g022-prep-3-tools/cap.py --ping text"
P3="$PY g022-prep-3-tools/plan.py bitget3 --pool pool-2026-11-12.json"
$P3 --top 200 --kinds books1 --out subs/R1-B.json
$P3 --top 199 --from-rank 200 --kinds books1 --out subs/R1-C.json
echo "R1 $(date -u +%T)"
$CAP --out frames/R1-A-v3-399.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/S1-D.json > frames/R1-A.log 2>&1 &
$CAP --out frames/R1-B-v3-200.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/R1-B.json > frames/R1-B.log 2>&1 &
$CAP --out frames/R1-C-v3-199.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/R1-C.json > frames/R1-C.log 2>&1 &
$CAP --out frames/R1-D-v2-399.jsonl.zst --secs 90 --url wss://ws.bitget.com/v2/ws/public --msgs-file subs/V2-D.json > frames/R1-D.log 2>&1 &
wait
echo "done $(date -u +%T)"
touch chain4.done
