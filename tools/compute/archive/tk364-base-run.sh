#!/bin/bash
cd /data/tk364
exec >>/data/tk364/run.log 2>&1
python3 /data/tk364/tk364-base-swaps.py /data/tk364/out 2026-07-01 2026-10-01 --threads-drpc 3
touch /data/tk364/out/.finished
