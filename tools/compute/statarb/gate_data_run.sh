#!/usr/bin/env bash
# TK-484: ворота данных П-32 §4 — probe ×3 биржи → build. rc → /data/sched/rc/<id> пишет alsched.
set -e
cd /data/tk0484
S=/data/tk0484/src/gate_data.py
C=/srv/alpha-sda/candles-1m
mkdir -p daily out
for ex in binance-um bybit-linear gate-futures_usdt; do
  [ -s daily/daily-$ex.csv.done ] || { python3 $S probe --dir $C/$ex --out daily/daily-$ex.csv --jobs 15 && touch daily/daily-$ex.csv.done; }
done
python3 $S build --daily-dir daily --universe-dir /data/tk0476/universe --coverage /data/tk0476/coverage.csv \
  --out-dir out --date 2026-11-03 \
  --exclude-absent "bybit_empty_symbols=BUZZUSDT|CANTOUSDT|GEMSUSDT|JAILSTOOLUSDT|KUSDT|MAPOUSDT|NODEUSDT|PENGUSDT" \
  --exclude-absent "bybit_absent_symbol_days=25336" \
  --exclude-window "gate-futures_usdt:2026-09-30T08:00:2026-09-30T08:31" \
  --exclude-window "binance-um:2026-10-28T00:00:2026-10-30T23:59"
ls -la out
