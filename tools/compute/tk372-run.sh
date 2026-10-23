#!/usr/bin/env bash
# TK-372: скачивание сделок Gate 15–21.10 (спот → фьюч), затем сверка с свечами. Докачка: готовые файлы пропускаются.
R=/srv/alpha-sda/tk372-gate; S=/data/tk0372/tk372-gate-dl.py; cd $R || exit 1; mkdir -p log
python3 $S run --root $R --workers 4 --rps 15 >> log/run.log 2>&1; rc=$?
echo "run rc=$rc $(date +%T)" >> log/run.log
[ $rc = 0 ] && python3 $S check --root $R --workers 2 --rps 8 >> log/check.log 2>&1
echo "check rc=$? $(date +%T)" >> log/run.log
