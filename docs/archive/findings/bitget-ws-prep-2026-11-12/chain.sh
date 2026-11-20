#!/bin/bash
# Цепочка проб Bitget (srv, /data/bitget-probe): P1 топ-100, P2 весь пул, P3 входы, P4 тишина/пинг. Окна 180 с, ≤ 5 соединений одновременно. Запуск: systemd-run --unit=bgprobe-chain bash chain.sh
cd /data/bitget-probe
PY=/root/bnws-venv/bin/python
CAP="$PY g022-prep-3-tools/cap.py"
PLAN="$PY g022-prep-3-tools/plan.py bitget --pool pool-2026-11-12.json"
U=wss://ws.bitget.com/v2/ws/public
mkdir -p subs frames
$PLAN --top 100 --kinds books1 --out subs/P1-A.json
$PLAN --top 100 --kinds trade --out subs/P1-B.json
$PLAN --top 100 --kinds ticker --out subs/P1-C.json
$PLAN --top 10 --kinds books1,books5,ticker,trade --out subs/P1-D.json
$PLAN --kinds books1 --out subs/P2-A.json
$PLAN --kinds trade --out subs/P2-B.json
$PLAN --kinds ticker --out subs/P2-C.json
$PLAN --kinds books1,trade,ticker --out subs/P2-D.json
$PLAN --top 10 --kinds books1,ticker,trade --out subs/P3.json
$PLAN --top 1 --kinds ticker --out subs/P4-one.json
echo '[]' > subs/none.json
echo "P1 $(date -u +%T)"
for k in A B C D; do $CAP --out frames/P1-$k.jsonl.zst --secs 180 --url $U --msgs-file subs/P1-$k.json --ping text > frames/P1-$k.log 2>&1 & done; wait
echo "P2 $(date -u +%T)"
for k in A B C D; do $CAP --out frames/P2-$k.jsonl.zst --secs 180 --url $U --msgs-file subs/P2-$k.json --ping text > frames/P2-$k.log 2>&1 & done; wait
echo "P3 $(date -u +%T)"
i=0
for ip in 18.154.63.54 18.154.63.61; do $CAP --out frames/P3-ws-$ip.jsonl.zst --secs 180 --url $U --ip $ip --msgs-file subs/P3.json --ping text > frames/P3-ws-$ip.log 2>&1 & done
for ip in 13.114.218.10 35.74.188.193 57.182.0.139; do $CAP --out frames/P3-vip-$ip.jsonl.zst --secs 180 --url wss://vip-ws.bitget.com/v2/ws/public --ip $ip --msgs-file subs/P3.json --ping text > frames/P3-vip-$ip.log 2>&1 & done; wait
echo "P4 $(date -u +%T)"
$CAP --out frames/P4-noping-data.jsonl.zst --secs 170 --url $U --msgs-file subs/P4-one.json --ping none > frames/P4-noping-data.log 2>&1 &
$CAP --out frames/P4-proto-data.jsonl.zst --secs 170 --url $U --msgs-file subs/P4-one.json --ping proto --ping-every 20 > frames/P4-proto-data.log 2>&1 &
$CAP --out frames/P4-text-nosub.jsonl.zst --secs 170 --url $U --msgs-file subs/none.json --ping text --ping-every 30 > frames/P4-text-nosub.log 2>&1 &
$CAP --out frames/P4-noping-nosub.jsonl.zst --secs 170 --url $U --msgs-file subs/none.json --ping none > frames/P4-noping-nosub.log 2>&1 &
wait
echo "done $(date -u +%T)"
touch chain.done
