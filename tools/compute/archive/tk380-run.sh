#!/bin/bash
# TK-380: свопы 15–22.10 (включительно) всех EVM-пулов монет Gate ≥30k$; готовые пул-сутки TK-364 переиспользуются (hardlink), качается дельта. Итог /data/tk0380/out/DONE.json
cd /data/tk0380
set -a; . /root/tk0364/nodereal.env; set +a
exec >>/data/tk0380/run.log 2>&1
O=/data/tk0380/out; P=/data/tk0364/tk364-evm-swaps.py; J=/data/tk0380/pools.json
for c in ethereum bsc base; do mkdir -p $O/$c; [ -d /data/tk0364/outw3/$c ] && cp -al /data/tk0364/outw3/$c/. $O/$c/; done
for c in ethereum bsc base; do
 python3 -u $P $O/$c 2026-10-15 2026-10-23 $c $J --threads 3 &
done
wait
echo '{"done":true}' > $O/DONE.json
