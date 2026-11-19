#!/bin/bash
# Доп. окна: Bitget — два SBE и два v3 JSON на одних символах (дедуп линий по seq); HTX — две линии trade.detail на «быстрых» соединениях (дедуп по id).
PY=/root/bnws-venv/bin/python
cd /data/bitget-probe
P3="$PY g022-prep-3-tools/plan.py bitget3 --pool pool-2026-11-12.json"
$P3 --from-rank 10 --top 30 --kinds books1 --out subs/D1.json
CAP="nice -n -10 $PY g022-prep-3-tools/cap.py --ping text"
echo "D1 $(date -u +%T)"
$CAP --out frames/D1-A-sbe.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public/sbe --msgs-file subs/D1.json > frames/D1-A.log 2>&1 &
$CAP --out frames/D1-B-sbe.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public/sbe --msgs-file subs/D1.json > frames/D1-B.log 2>&1 &
$CAP --out frames/D1-C-v3json.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/D1.json > frames/D1-C.log 2>&1 &
$CAP --out frames/D1-D-v3json.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/D1.json > frames/D1-D.log 2>&1 &
cd /data/htx-probe
rm -f frames/D1-*.tries
F="bash g022-prep-3-tools/fast.sh"
$PY g022-prep-3-tools/plan.py htx --pool pool-2026-11-12.json --top 20 --kinds trade.detail --out subs/D1-trade.json
$F frames/D1-A-trade.jsonl.zst 10 --secs 90 --url wss://api.hbdm.com/linear-swap-ws --msgs-file subs/D1-trade.json --gz --htx-pong --sub-pause 0.03 > frames/D1-A.log 2>&1 &
$F frames/D1-B-trade.jsonl.zst 10 --secs 90 --url wss://api.hbdm.com/linear-swap-ws --msgs-file subs/D1-trade.json --gz --htx-pong --sub-pause 0.03 > frames/D1-B.log 2>&1 &
wait
echo "done $(date -u +%T)"
touch /data/chainx.done
