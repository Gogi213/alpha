#!/bin/bash
# Цепочка 2 Bitget (srv, /data/bitget-probe): V1 UTA v3 топ-100 + сравнение с v2, V2 UTA v3 весь пул, V3 проверка лимита 1000 подписок (v2, 2394 аргументов). Запуск: systemd-run --unit=bgprobe-chain2 bash chain2.sh
cd /data/bitget-probe
PY=/root/bnws-venv/bin/python
CAP="$PY g022-prep-3-tools/cap.py --ping text"
P2="$PY g022-prep-3-tools/plan.py bitget --pool pool-2026-11-12.json"
P3="$PY g022-prep-3-tools/plan.py bitget3 --pool pool-2026-11-12.json"
U2=wss://ws.bitget.com/v2/ws/public
U3=wss://ws.bitget.com/v3/ws/public
$P3 --top 100 --kinds books1 --out subs/V1-A.json
$P3 --top 100 --kinds publicTrade --out subs/V1-B.json
$P3 --top 100 --kinds ticker --out subs/V1-C.json
$P3 --top 10 --kinds books1,books5,ticker,publicTrade --out subs/V1-D.json
$P2 --top 10 --kinds books1,ticker,trade --out subs/V1-E.json
$P3 --kinds books1 --out subs/V2-A.json
$P3 --kinds publicTrade --out subs/V2-B.json
$P3 --kinds ticker --out subs/V2-C.json
$P2 --kinds books1 --out subs/V2-D.json
$P2 --kinds books1,trade,ticker,candle1m,candle5m,candle15m --out subs/V3-A.json
echo "V1 $(date -u +%T)"
for k in A B C D; do $CAP --out frames/V1-$k.jsonl.zst --secs 180 --url $U3 --msgs-file subs/V1-$k.json > frames/V1-$k.log 2>&1 & done
$CAP --out frames/V1-E-v2.jsonl.zst --secs 180 --url $U2 --msgs-file subs/V1-E.json > frames/V1-E.log 2>&1 &
wait
echo "V2 $(date -u +%T)"
for k in A B C; do $CAP --out frames/V2-$k.jsonl.zst --secs 180 --url $U3 --msgs-file subs/V2-$k.json > frames/V2-$k.log 2>&1 & done
$CAP --out frames/V2-D-v2.jsonl.zst --secs 180 --url $U2 --msgs-file subs/V2-D.json > frames/V2-D.log 2>&1 &
wait
echo "V3 $(date -u +%T)"
$CAP --out frames/V3-A-v2-2394args.jsonl.zst --secs 60 --url $U2 --msgs-file subs/V3-A.json > frames/V3-A.log 2>&1
echo "done $(date -u +%T)"
touch chain2.done
