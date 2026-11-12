#!/usr/bin/env bash
# TK-375: сделки Gate 15–21.10 по монетам, ОБЩИМ с Bybit (list-*-common.csv), в тот же raw/ TK-372. Докачка: готовые сутки пропускаются.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
R=/srv/alpha-sda/tk372-gate; S=/data/tk0375/tk372-gate-dl.py; cd $R || exit 1
python3 $S run --root $R --workers 4 --rps 15 --lists=-common >> log/run.log 2>&1; rc=$?
echo "tk375 run rc=$rc $(date +%T)" >> log/run.log
[ $rc = 0 ] || exit $rc
python3 $S check --root $R --workers 2 --rps 8 --interval 1m --lists=-common --days 2026-10-17,2026-10-18,2026-10-19,2026-10-20,2026-10-21 >> log/check.log 2>&1
echo "tk375 check1m rc=$? $(date +%T)" >> log/run.log
python3 $S check --root $R --workers 2 --rps 8 --interval 5m --lists=-common --days 2026-10-15,2026-10-16 >> log/check.log 2>&1
echo "tk375 check5m rc=$? $(date +%T)" >> log/run.log