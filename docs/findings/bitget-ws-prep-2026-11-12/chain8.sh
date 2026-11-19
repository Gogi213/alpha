#!/bin/bash
# Цепочка 8 Bitget (srv): M1 — SBE books1 топ-100, разбитые на 5 соединений по рангу оборота ([0,4) [4,12) [12,30) [30,60) [60,100)), 150 с, клиенты nice -10, на машине ничего больше.
cd /data/bitget-probe
P3="/root/bnws-venv/bin/python g022-prep-3-tools/plan.py bitget3 --pool pool-2026-11-12.json"
CAP="nice -n -10 /root/bnws-venv/bin/python g022-prep-3-tools/cap.py --ping text"
i=0
for rng in "0 4" "4 8" "12 18" "30 30" "60 40"; do set -- $rng; $P3 --from-rank $1 --top $2 --kinds books1 --out subs/M1-$i.json; i=$((i+1)); done
echo "M1 $(date -u +%T) load $(cut -d' ' -f1-3 /proc/loadavg)"
for k in 0 1 2 3 4; do $CAP --out frames/M1-$k-sbe.jsonl.zst --secs 150 --url wss://ws.bitget.com/v3/ws/public/sbe --msgs-file subs/M1-$k.json > frames/M1-$k.log 2>&1 & done
wait
echo "done $(date -u +%T) load $(cut -d' ' -f1-3 /proc/loadavg)"
touch chain8.done
