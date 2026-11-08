#!/bin/bash
# TK-500: оркестратор orch.py на сервере: DOGE, пара 1 (Binance x OKX) = прежние таблицы байт в байт (/tmp/old.csv*, см. srv-test.sh old); затем 3 пары с общей ногой A × 4 суток, --jobs 2 (замер C и сети).
# Файлы pairs_doge.csv / pairs_doge3.csv — в /root/p40core (asset,pair,exA,symA,multA,lotA,stepA,exB,symB,multB,lotB,stepB).
cd /root/p40core || exit 1
rm -rf /tmp/orch1 /tmp/orch4
python3 orch.py pairs_doge.csv /tmp/orch1 --days 2026-09-17 --jobs 1 2>&1 | tail -2
O=/tmp/orch1/1/2026-09-17
for f in "" .fills.csv .exits.csv .features.csv .path.csv; do cmp /tmp/old.csv$f $O/1$f && echo "байт в байт: $f"; done
python3 orch.py pairs_doge3.csv /tmp/orch4 --days 2026-09-17,2026-09-18,2026-09-19,2026-09-20 --jobs 2 2>&1 | tail -5
cat /tmp/orch4/orch.csv; uptime
