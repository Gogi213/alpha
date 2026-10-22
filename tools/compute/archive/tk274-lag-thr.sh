#!/bin/bash
# TK-274 (П-15 Г-72): пороги T_m(lag, q) по секундному ряду BTC (/data/tk204/btc-seconds) -> btc-lag-thresholds.json.
# Запуск только через alsched (--kind gate --cores 1): читает 279 файлов суток.
set -euo pipefail
cd /data/tk0274/p15
python3 btc-lag-thresholds.py --btc-seconds /data/tk204/btc-seconds --out btc-lag-thresholds.json
echo ok > lag-thr.rc
