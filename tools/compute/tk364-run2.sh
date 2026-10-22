#!/bin/bash
# TK-364: вторая выгрузка (хайкапы + мажоры) — ETH, BSC, Base параллельно; общий итог /data/tk0364/out2/DONE.json
cd /data/tk0364
set -a; . /root/tk0364/nodereal.env; set +a
exec >>/data/tk0364/run2.log 2>&1
P=/data/tk0364/tk364-evm-swaps.py; J=/data/tk0364/tk364-pools2.json; O=/data/tk0364/out2
mkdir -p $O
python3 -u $P $O/ethereum 2026-07-01 2026-10-01 ethereum $J --threads 3 &
python3 -u $P $O/bsc 2026-07-01 2026-10-01 bsc $J --threads 2 &
python3 -u $P $O/base 2026-08-17 2026-10-01 base $J --threads 2 &
wait
python3 - <<PY
import json,os
r={c:json.load(open(f"$O/{c}/DONE.json")) if os.path.exists(f"$O/{c}/DONE.json") else None for c in ("ethereum","bsc","base")}
json.dump(r,open("$O/DONE.json","w"))
PY
