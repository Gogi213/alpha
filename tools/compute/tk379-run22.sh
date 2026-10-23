#!/usr/bin/env bash
# TK-379: окно продлено владельцем до 22.10 включительно — докачка суток 2026-10-22 по ВСЕМ спискам (TK-372, TK-375, TK-379 d30) + сверка свечами 1м. Готовые сутки пропускаются.
export PYTHONUTF8=1 LC_ALL=C.UTF-8
R=/srv/alpha-sda/tk372-gate; S=/data/tk0379/tk372-gate-dl.py; cd $R || exit 1
for L in "" -common -d30; do
  python3 $S run --root $R --workers 4 --rps 15 --lists=$L --days 2026-10-22 >> log/run.log 2>&1; rc=$?
  echo "tk379 run22 lists='$L' rc=$rc $(date +%T)" >> log/run.log
  [ $rc = 0 ] || exit $rc
done
for L in "" -common -d30; do
  python3 $S check --root $R --workers 2 --rps 8 --interval 1m --lists=$L --days 2026-10-22 >> log/check.log 2>&1
  echo "tk379 check22 lists='$L' rc=$? $(date +%T)" >> log/run.log
done
