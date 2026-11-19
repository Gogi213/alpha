#!/bin/bash
# Цепочка 3 Bitget (srv, /data/bitget-probe): S1 — SBE books1 (бинарный, /v3/ws/public/sbe) против JSON v3 и v2 books1 на одних и тех же 100 символах одновременно + SBE весь пул. 180 с, 5 соединений.
cd /data/bitget-probe
PY=/root/bnws-venv/bin/python
CAP="$PY g022-prep-3-tools/cap.py --ping text"
P2="$PY g022-prep-3-tools/plan.py bitget --pool pool-2026-11-12.json"
P3="$PY g022-prep-3-tools/plan.py bitget3 --pool pool-2026-11-12.json"
$P3 --top 100 --kinds books1 --out subs/S1-A.json
$P3 --kinds books1 --out subs/S1-D.json
$P2 --top 100 --kinds books1 --out subs/S1-C.json
echo "S1 $(date -u +%T)"
$CAP --out frames/S1-A-sbe100.jsonl.zst --secs 180 --url wss://ws.bitget.com/v3/ws/public/sbe --msgs-file subs/S1-A.json > frames/S1-A.log 2>&1 &
$CAP --out frames/S1-B-v3json100.jsonl.zst --secs 180 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/S1-A.json > frames/S1-B.log 2>&1 &
$CAP --out frames/S1-C-v2json100.jsonl.zst --secs 180 --url wss://ws.bitget.com/v2/ws/public --msgs-file subs/S1-C.json > frames/S1-C.log 2>&1 &
$CAP --out frames/S1-D-sbe399.jsonl.zst --secs 180 --url wss://ws.bitget.com/v3/ws/public/sbe --msgs-file subs/S1-D.json > frames/S1-D.log 2>&1 &
wait
echo "done $(date -u +%T)"
touch chain3.done
