#!/bin/bash
# Цепочка 7 Bitget (srv): C1 — чистый перезамер без параллельного разбора на машине (разбор во время окон душил клиента: обрывы на 6–30 с и хвосты лага были при моём одновременном счёте). 150 с, 5 соединений, клиенты nice -10.
cd /data/bitget-probe
CAP="nice -n -10 /root/bnws-venv/bin/python g022-prep-3-tools/cap.py --ping text"
echo "C1 $(date -u +%T) load $(cut -d' ' -f1-3 /proc/loadavg)"
$CAP --out frames/C1-A-sbe100.jsonl.zst --secs 150 --url wss://ws.bitget.com/v3/ws/public/sbe --msgs-file subs/S1-A.json > frames/C1-A.log 2>&1 &
$CAP --out frames/C1-B-v3json100.jsonl.zst --secs 150 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/S1-A.json > frames/C1-B.log 2>&1 &
$CAP --out frames/C1-C-v2json100.jsonl.zst --secs 150 --url wss://ws.bitget.com/v2/ws/public --msgs-file subs/S1-C.json > frames/C1-C.log 2>&1 &
$CAP --out frames/C1-D-v3json399.jsonl.zst --secs 150 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/S1-D.json > frames/C1-D.log 2>&1 &
$CAP --out frames/C1-E-sbe399.jsonl.zst --secs 150 --url wss://ws.bitget.com/v3/ws/public/sbe --msgs-file subs/S1-D.json > frames/C1-E.log 2>&1 &
wait
echo "done $(date -u +%T) load $(cut -d' ' -f1-3 /proc/loadavg)"
touch chain7.done
