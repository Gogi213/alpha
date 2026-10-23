#!/bin/bash
# TK-380: свопы 15–22.10 пулов Base/ETH монет Gate ≥30k$ через публичный drpc (NodeReal исчерпал квоту 23.10; BSC без ключа недоступен). Выход /data/tk0380/out2/<chain>/<SYM>/<день>.csv, DONE.json
cd /data/tk0380
exec >>/data/tk0380/run2.log 2>&1
O=/data/tk0380/out2; P=/data/tk0364/tk364-evm-multi.py; J=/data/tk0380/pools.json
D=2026-10-22,2026-10-21,2026-10-20,2026-10-19,2026-10-18,2026-10-17,2026-10-16,2026-10-15
mkdir -p $O
python3 -u $P $O base $J https://base.drpc.org $D --skip-dir /data/tk0364/outw3 --threads 3 &
python3 -u $P $O ethereum $J https://eth.drpc.org $D --skip-dir /data/tk0364/outw3 --threads 3 &
wait
echo '{"done":true}' > $O/ALLDONE.json
