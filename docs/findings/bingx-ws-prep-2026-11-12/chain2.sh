#!/bin/bash
# Цепочка 2 BingX (srv): E1 — входы, 90 с, 5 соединений: open-api-swap.bingx.com по двум IPv4 и двум IPv6 (CloudFront) и open-api-ws.bingx.com/market (хост из английской документации); топ-10 × bookTicker, trade, markPrice.
cd /data/bingx-probe
while [ ! -e chain.done ]; do sleep 2; done
PY=/root/bnws-venv/bin/python
CAP="nice -n -10 $PY g022-prep-3-tools/cap.py --gz --bingx-pong --sub-pause 0.02"
$PY g022-prep-3-tools/plan.py bingx --pool pool-2026-11-12.json --top 10 --kinds bookTicker,trade,markPrice --out subs/E1.json
U=wss://open-api-swap.bingx.com/swap-market
echo "E1 $(date -u +%T)"
$CAP --out frames/E1-v4-54.230.172.49.jsonl.zst --secs 90 --url $U --ip 54.230.172.49 --msgs-file subs/E1.json > frames/E1-A.log 2>&1 &
$CAP --out frames/E1-v4-54.230.172.82.jsonl.zst --secs 90 --url $U --ip 54.230.172.82 --msgs-file subs/E1.json > frames/E1-B.log 2>&1 &
$CAP --out frames/E1-v6-a.jsonl.zst --secs 90 --url $U --ip 2600:9000:201c:1200:1f:3dec:7600:93a1 --msgs-file subs/E1.json > frames/E1-C.log 2>&1 &
$CAP --out frames/E1-v6-b.jsonl.zst --secs 90 --url $U --ip 2600:9000:201c:5800:1f:3dec:7600:93a1 --msgs-file subs/E1.json > frames/E1-D.log 2>&1 &
$CAP --out frames/E1-openapi-ws-market.jsonl.zst --secs 90 --url wss://open-api-ws.bingx.com/market --msgs-file subs/E1.json > frames/E1-E.log 2>&1 &
wait
echo "done $(date -u +%T)"
touch chain2.done
