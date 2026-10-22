#!/bin/bash
# TK-364: недельная выгрузка 15–21.10.2026 (окно G-016): ядро BSC + хайкапы/мажоры ETH/BSC/Base; итог /data/tk0364/outw/DONE.json
cd /data/tk0364
set -a; . /root/tk0364/nodereal.env; set +a
exec >>/data/tk0364/runw.log 2>&1
P=/data/tk0364/tk364-evm-swaps.py; J=/data/tk0364/tk364-pools2.json; O=/data/tk0364/outw
mkdir -p $O
python3 -u /data/tk0364/tk364-bsc-swaps.py $O/bsc-core 2026-10-15 2026-10-22 --threads-drpc 3 &
python3 -u $P $O/ethereum 2026-10-15 2026-10-22 ethereum $J --threads 3 &
python3 -u $P $O/bsc 2026-10-15 2026-10-22 bsc $J --threads 2 &
python3 -u $P $O/base 2026-10-15 2026-10-22 base $J --threads 2 &
wait
echo '{"done":true}' > $O/DONE.json
