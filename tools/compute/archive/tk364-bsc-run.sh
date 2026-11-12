#!/bin/bash
cd /data/tk0364
set -a; . /root/tk0364/nodereal.env; set +a
exec >>/data/tk0364/run-bsc.log 2>&1
python3 -u /data/tk0364/tk364-bsc-swaps.py /data/tk0364/out-bsc 2026-07-01 2026-10-01 --threads-drpc 4
