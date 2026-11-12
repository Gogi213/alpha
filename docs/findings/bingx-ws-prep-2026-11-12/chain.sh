#!/bin/bash
# Цепочка проб BingX (srv, /data/bingx-probe): W1 топ-100 (+сравнение каналов), W2 весь пул bookTicker на 5 соединениях (по рангу mod 5, ≤ 200 тем), W3 trade, W4 markPrice (90 с), W5 лимиты/пинг.
# Запуск: systemd-run --unit=bxprobe-chain bash chain.sh. Клиенты nice -10.
cd /data/bingx-probe
PY=/root/bnws-venv/bin/python
CAP="nice -n -10 $PY g022-prep-3-tools/cap.py --gz --bingx-pong --sub-pause 0.02 --tcp-probe"
PLAN="$PY g022-prep-3-tools/plan.py bingx --pool pool-2026-11-12.json"
U=wss://open-api-swap.bingx.com/swap-market
mkdir -p subs frames
$PLAN --top 100 --kinds bookTicker --out subs/W1-A.json
$PLAN --top 100 --kinds trade --out subs/W1-B.json
$PLAN --top 100 --kinds markPrice --out subs/W1-C.json
$PLAN --top 10 --kinds bookTicker,depth5@200ms,incrDepth,ticker,lastPrice --out subs/W1-D.json
$PY - <<'PY'
import json
syms = list(json.load(open("/data/bingx-probe/pool-2026-11-12.json"))["qv"])
for kind in ("bookTicker", "trade", "markPrice"):
    for k in range(5):
        json.dump([{"id": f"{kind}-{s}", "reqType": "sub", "dataType": f"{s}@{kind}"} for s in syms[k::5]], open(f"/data/bingx-probe/subs/F-{kind}-{k}.json", "w"))
json.dump([{"id": f"bt-{s}", "reqType": "sub", "dataType": f"{s}@bookTicker"} for s in syms[:200]], open("/data/bingx-probe/subs/W5-200.json", "w"))
json.dump([{"id": f"bt-{s}", "reqType": "sub", "dataType": f"{s}@bookTicker"} for s in syms[:201]], open("/data/bingx-probe/subs/W5-201.json", "w"))
print("pool", len(syms), "per conn", len(syms[0::5]))
PY
$PLAN --top 1 --kinds bookTicker --out subs/W5-one.json
echo '[]' > subs/none.json
echo "W1 $(date -u +%T)"
for k in A B C D; do $CAP --out frames/W1-$k.jsonl.zst --secs 180 --url $U --msgs-file subs/W1-$k.json > frames/W1-$k.log 2>&1 & done; wait
echo "W2 $(date -u +%T)"
for k in 0 1 2 3 4; do $CAP --out frames/W2-bookTicker-$k.jsonl.zst --secs 180 --url $U --msgs-file subs/F-bookTicker-$k.json > frames/W2-$k.log 2>&1 & done; wait
echo "W3 $(date -u +%T)"
for k in 0 1 2 3 4; do $CAP --out frames/W3-trade-$k.jsonl.zst --secs 90 --url $U --msgs-file subs/F-trade-$k.json > frames/W3-$k.log 2>&1 & done; wait
echo "W4 $(date -u +%T)"
for k in 0 1 2 3 4; do $CAP --out frames/W4-markPrice-$k.jsonl.zst --secs 90 --url $U --msgs-file subs/F-markPrice-$k.json > frames/W4-$k.log 2>&1 & done; wait
echo "W5 $(date -u +%T)"
$CAP --out frames/W5-A-200topics.jsonl.zst --secs 100 --url $U --msgs-file subs/W5-200.json > frames/W5-A.log 2>&1 &
$CAP --out frames/W5-B-201topics.jsonl.zst --secs 100 --url $U --msgs-file subs/W5-201.json > frames/W5-B.log 2>&1 &
$PY g022-prep-3-tools/cap.py --gz --tcp-probe --out frames/W5-C-nopong-data.jsonl.zst --secs 100 --url $U --msgs-file subs/W5-one.json > frames/W5-C.log 2>&1 &
$PY g022-prep-3-tools/cap.py --gz --tcp-probe --out frames/W5-D-nopong-nosub.jsonl.zst --secs 100 --url $U --msgs-file subs/none.json > frames/W5-D.log 2>&1 &
$CAP --out frames/W5-E-pong-nosub.jsonl.zst --secs 100 --url $U --msgs-file subs/none.json > frames/W5-E.log 2>&1 &
wait
echo "done $(date -u +%T)"
touch chain.done
