#!/bin/bash
# Цепочка проб HTX (srv, /data/htx-probe): H1 пул (49), H2 весь рынок + индекс + входы, H3 тишина/пинг. Окна 180 с (H3 — 100 с), ≤ 5 соединений одновременно.
# Запуск: systemd-run --unit=htxprobe-chain bash chain.sh (клиенты nice -10; разбор на машине — nice 19)
cd /data/htx-probe
PY=/root/bnws-venv/bin/python
CAP="nice -n -10 $PY g022-prep-3-tools/cap.py --gz --htx-pong"
PLAN="$PY g022-prep-3-tools/plan.py htx --pool pool-2026-11-12.json"
M=wss://api.hbdm.com/linear-swap-ws
mkdir -p subs frames
$PLAN --kinds bbo --out subs/H1-A.json
$PLAN --kinds trade.detail --out subs/H1-B.json
$PLAN --kinds mark_price.1min --out subs/H1-C.json
$PLAN --top 10 --kinds bbo,detail,depth.step6,depth.size_20.high_freq --out subs/H1-D.json
echo '[{"op":"sub","topic":"public.*.funding_rate","cid":"fr"}]' > subs/H1-E.json
$PLAN --kinds index.1min --out subs/H2-A.json
# весь рынок: контракты из swap_contract_info (status 1), bbo
python3 - <<'EOF'
import json
c = json.load(open("/data/htx-probe/ci-api.hbdm.com.json"))["data"]
codes = [i["contract_code"] for i in c if i["business_type"] == "swap" and i["contract_status"] == 1 and i["trade_partition"] == "USDT"]
json.dump([{"sub": f"market.{s}.bbo", "id": f"bbo-{s}"} for s in codes], open("/data/htx-probe/subs/H2-B.json", "w"))
print("all", len(codes))
EOF
$PLAN --top 10 --kinds bbo,trade.detail,mark_price.1min --out subs/H2-E.json
$PLAN --top 1 --kinds bbo --out subs/H3-one.json
echo '[]' > subs/none.json
echo "H1 $(date -u +%T)"
for k in A B C D; do $CAP --out frames/H1-$k.jsonl.zst --secs 180 --url $M --msgs-file subs/H1-$k.json --sub-pause 0.03 > frames/H1-$k.log 2>&1 & done
$CAP --out frames/H1-E-notif.jsonl.zst --secs 180 --url wss://api.hbdm.com/linear-swap-notification --msgs-file subs/H1-E.json > frames/H1-E.log 2>&1 &
wait
echo "H2 $(date -u +%T)"
$CAP --out frames/H2-A-index.jsonl.zst --secs 180 --url wss://api.hbdm.com/ws_index --msgs-file subs/H2-A.json --sub-pause 0.03 > frames/H2-A.log 2>&1 &
$CAP --out frames/H2-B-all.jsonl.zst --secs 180 --url $M --msgs-file subs/H2-B.json --sub-pause 0.03 > frames/H2-B.log 2>&1 &
for h in api.hbdm.com api.hbdm.vn api.btcgateway.pro; do $CAP --out frames/H2-ent-$h.jsonl.zst --secs 180 --url wss://$h/linear-swap-ws --msgs-file subs/H2-E.json --sub-pause 0.03 > frames/H2-ent-$h.log 2>&1 & done
wait
echo "H3 $(date -u +%T)"
nice -n -10 $PY g022-prep-3-tools/cap.py --gz --out frames/H3-nopong-data.jsonl.zst --secs 100 --url $M --msgs-file subs/H3-one.json > frames/H3-nopong-data.log 2>&1 &
nice -n -10 $PY g022-prep-3-tools/cap.py --gz --out frames/H3-nopong-nosub.jsonl.zst --secs 100 --url $M --msgs-file subs/none.json > frames/H3-nopong-nosub.log 2>&1 &
$CAP --out frames/H3-pong-data.jsonl.zst --secs 100 --url $M --msgs-file subs/H3-one.json > frames/H3-pong-data.log 2>&1 &
$CAP --out frames/H3-pong-nosub.jsonl.zst --secs 100 --url $M --msgs-file subs/none.json > frames/H3-pong-nosub.log 2>&1 &
wait
echo "done $(date -u +%T)"
touch chain.done
