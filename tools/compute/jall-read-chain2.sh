#!/usr/bin/env bash
# TK-018: перезапуск чтения jall после потери задачи пула (earlyoom убил воркер 14:52:24 → imap повис бы навсегда):
# --resume берёт готовые строки с диска (<имя>/ps-closes.json), --jobs 0 — воркеров по памяти (3 ГБ на воркер, TK-019).
# Дальше — как jall-read-chain.sh: выгрузка → ждать j9-read → маркер jall-read.finished.
T=/home/deck/alpha/tmp-p07
cd $T || exit 1
mkdir -p /home/deck/alpha/tmp-dash
python3 p07-all-jul-read.py --out $T/jall-read/jall-read.json --jobs 0 --resume > $T/jall-read.log 2>&1
rc=$?
[ $rc = 0 ] && python3 dash-jall-dump.py > /home/deck/alpha/tmp-dash/jall.json 2>> $T/jall-read.log
rc2=$?
until [ -e $T/j9-read.finished ]; do sleep 60; done
echo "read rc=$rc dump rc=$rc2 $(date -u +%FT%TZ)" > $T/jall-read.finished
