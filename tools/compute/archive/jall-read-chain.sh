#!/usr/bin/env bash
# TK-018 (дека): после сетки июля (jall-cells.finished) — чтение всех вариантов → выгрузка → ждать j9-read → маркер.
T=/home/deck/alpha/tmp-p07
until [ -e $T/jall-cells.finished ]; do sleep 60; done
cd $T || exit 1
mkdir -p /home/deck/alpha/tmp-dash
python3 p07-all-jul-read.py --out $T/jall-read/jall-read.json --jobs 0 > $T/jall-read.log 2>&1
rc=$?
[ $rc = 0 ] && python3 dash-jall-dump.py > /home/deck/alpha/tmp-dash/jall.json 2>> $T/jall-read.log
rc2=$?
until [ -e $T/j9-read.finished ]; do sleep 60; done
echo "read rc=$rc dump rc=$rc2 $(date -u +%FT%TZ)" > $T/jall-read.finished
