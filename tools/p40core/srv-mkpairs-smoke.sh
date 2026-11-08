#!/bin/bash
# TK-500: mkpairs.py на сервере: meta (OKX ctVal, Gate quanto) из публичного REST, символы G0 + список Binance с диска -> pairs.csv; проверка DOGE: lot авто ~ 12300 / 12.3 по orch.
cd /root/p40core || exit 1
python3 mkpairs.py --fetch meta.json
ls /data/stakan/chft/binance_futures/2026-09-17/00/ | sed -n 's/_orderbook.parquet$//p' > bin.txt; wc -l < bin.txt
python3 mkpairs.py --symbols symbols.json --binance bin.txt --meta meta.json --out pairs_all.csv
grep -E ",DOGE" pairs_all.csv | head -8; head -3 pairs_all.assets.csv
