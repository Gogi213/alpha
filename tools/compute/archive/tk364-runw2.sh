#!/bin/bash
# TK-364: недельная выгрузка 15–21.10, дополнение: все прочие монеты юниверса (pools3n) на ETH/BSC/Base; итог /data/tk0364/outw2/DONE.json
cd /data/tk0364
set -a; . /root/tk0364/nodereal.env; set +a
exec >>/data/tk0364/runw2.log 2>&1
P=/data/tk0364/tk364-evm-swaps.py; J=/data/tk0364/tk364-pools3n.json; O=/data/tk0364/outw2
mkdir -p $O
python3 -u $P $O/ethereum 2026-10-15 2026-10-22 ethereum $J --threads 3 &
python3 -u $P $O/bsc 2026-10-15 2026-10-22 bsc $J --threads 3 &
python3 -u $P $O/base 2026-10-15 2026-10-22 base $J --threads 2 &
wait
echo '{"done":true}' > $O/DONE.json
