#!/bin/bash
# Цепочка 5 Bitget (srv): L — проверка «Max 1000 channel subscription/connection» дешёвыми каналами (trade по всем 885 контрактам + свечи 1D/1W), 60 с, 2 соединения.
cd /data/bitget-probe
while [ ! -e chain4.done ]; do sleep 2; done
python3 - <<'PY'
import json
syms = [t["symbol"] for t in json.load(open("/data/bitget-probe/tickers.json"))["data"]]
def msgs(kinds):
    args = [{"instType": "USDT-FUTURES", "channel": k, "instId": s} for k, ss in kinds for s in ss]
    return [{"op": "subscribe", "args": args[i:i + 40]} for i in range(0, len(args), 40)], len(args)
for name, kinds in (("L-A", [("trade", syms), ("candle1D", syms[:300])]), ("L-B", [("trade", syms), ("candle1D", syms), ("candle1W", syms)])):
    m, n = msgs(kinds)
    json.dump(m, open(f"/data/bitget-probe/subs/{name}.json", "w"))
    print(name, "args", n, "messages", len(m))
PY
CAP="/root/bnws-venv/bin/python g022-prep-3-tools/cap.py --ping text"
echo "L $(date -u +%T)"
$CAP --out frames/L-A-1185args.jsonl.zst --secs 60 --url wss://ws.bitget.com/v2/ws/public --msgs-file subs/L-A.json > frames/L-A.log 2>&1 &
$CAP --out frames/L-B-2655args.jsonl.zst --secs 60 --url wss://ws.bitget.com/v2/ws/public --msgs-file subs/L-B.json > frames/L-B.log 2>&1 &
wait
echo "done $(date -u +%T)"
touch chain5.done
