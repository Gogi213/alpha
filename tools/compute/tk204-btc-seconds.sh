#!/bin/sh
python3 /data/tk204/tk204-btc-seconds.py --from 2025-12-31 --to 2026-10-02 --out /data/tk204/btc-seconds > /data/tk204/btc-seconds.log 2>&1
echo $? > /data/tk204/btc-seconds.rc
