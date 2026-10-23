#!/usr/bin/env bash
# TK-372: докачка 4 китайских символов (URL-кодирование) + сверка 15–16.10 свечами 5м (1м живут ≈7 сут) и новых монет 17–21.10 свечами 1м.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
R=/srv/alpha-sda/tk372-gate; S=/data/tk0372/tk372-gate-dl.py; cd $R || exit 1
python3 $S run --root $R --workers 4 --rps 15 >> log/run.log 2>&1; echo "redo-run rc=$? $(date +%T)" >> log/run.log
python3 $S check --root $R --workers 2 --rps 8 --interval 5m --days 2026-10-15,2026-10-16 >> log/check.log 2>&1; echo "redo-check5m rc=$? $(date +%T)" >> log/run.log
python3 $S check --root $R --workers 2 --rps 8 --interval 1m --days 2026-10-17,2026-10-18,2026-10-19,2026-10-20,2026-10-21 --only '龙虾_USDT,牛来_USDT,币安人生_USDT,哈基米_USDT' >> log/check.log 2>&1; echo "redo-check1m rc=$? $(date +%T)" >> log/run.log
