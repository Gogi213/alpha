#!/bin/bash
# TK-500: auto-размер заявки в orch.py: DOGE Binance x OKX 17.09, lot пусто, ctB=1000 (OKX ctVal DOGE) -> lots.csv ~ 1000 USDT/ногу; затем сравнение с явным 12300 / 12.3 (pairs_doge.csv).
cd /root/p40core || exit 1
printf 'asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB,ctA,ctB\n1,1,binance,DOGEUSDT,,,,okx,DOGE-USDT-SWAP,,,,1,1000\n' > pairs_doge_auto.csv
rm -rf /tmp/orcha
python3 orch.py pairs_doge_auto.csv /tmp/orcha --days 2026-09-17 --jobs 1 2>&1 | tail -3
cat /tmp/orcha/lots.csv; head -c 300 /tmp/orcha/1/2026-09-17/1.fills.csv; uptime
