#!/bin/bash
# Цепочка 6 Bitget (srv): R2 — высокочастотные каналы по одному на машине без соседних проб (проверка: хвост лага — путь/сервер или загрузка клиента). 90 с каждое, последовательно: SBE top-100, затем JSON v3 top-100.
cd /data/bitget-probe
while [ ! -e chain5.done ]; do sleep 2; done
CAP="/root/bnws-venv/bin/python g022-prep-3-tools/cap.py --ping text"
echo "R2a $(date -u +%T)"
$CAP --out frames/R2-a-sbe100-alone.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public/sbe --msgs-file subs/S1-A.json > frames/R2-a.log 2>&1
echo "R2b $(date -u +%T)"
$CAP --out frames/R2-b-v3json100-alone.jsonl.zst --secs 90 --url wss://ws.bitget.com/v3/ws/public --msgs-file subs/S1-A.json > frames/R2-b.log 2>&1
echo "done $(date -u +%T)"
touch chain6.done
