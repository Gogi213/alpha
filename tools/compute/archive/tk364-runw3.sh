#!/bin/bash
# TK-364: недельная выгрузка 15–21.10, шаг 3: все монеты Bybit с DEX-пулом (перечисление tk364-enum.py) на ETH/BSC/Base; итог /data/tk0364/outw3/DONE.json
cd /data/tk0364
set -a; . /root/tk0364/nodereal.env; set +a
exec >>/data/tk0364/runw3.log 2>&1
O=/data/tk0364/outw3; P=/data/tk0364/tk364-evm-swaps.py
mkdir -p $O
python3 -u /data/tk0364/tk364-enum.py /data/tk0364/bybit-ticks.csv $O --known /data/tk0364/tk364-pools2.json,/data/tk0364/tk364-pools3n.json || exit 1
J=$O/pools4.json
python3 -u $P $O/ethereum 2026-10-15 2026-10-22 ethereum $J --threads 3 &
python3 -u $P $O/bsc 2026-10-15 2026-10-22 bsc $J --threads 3 &
python3 -u $P $O/base 2026-10-15 2026-10-22 base $J --threads 2 &
wait
echo '{"done":true}' > $O/DONE.json
