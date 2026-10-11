#!/bin/bash
# TK-037 В-172: гейт --steps-from-day на UNI 04-01 (байт в байт с v3) + боевой импорт 112 суток со сменой шага (v4). Журнал: $L/one-v4.log, маркер ALL_DONE_V4.
BIN=${BIN:-/opt/alpha-compute/bin/alpha-tk037-validate}; L=/data/tk037; P=${P:-4}
export BIN ROOT=$L/roots LOG=$L/one-v4.log DAYMODE=1 NC=1
ref=$(ls $L/vroots/e-2026-04/UNIUSDT-2026-04-01.binlog); G=$L/v4-gate; rm -rf $G; mkdir -p $G
RAW=${RAW:-/data/tk037raw}
unzip -p $RAW/ob-UNIUSDT-2026-04-01.zip > $G/ob
$BIN lob import-archive --symbol UNIUSDT --day 2026-04-01 --ob $G/ob --trades <(gunzip -c $RAW/tr-UNIUSDT-2026-04-01.csv.gz) --instruments $L/instruments.csv --root $G --steps-from-day > $G/out 2>&1
echo "gate rc=$? new=$(sha256sum $G/UNIUSDT-2026-04-01.binlog | cut -c1-16) ref=$(sha256sum $(readlink -f $ref) | cut -c1-16) cmp=$(cmp $G/UNIUSDT-2026-04-01.binlog $ref && echo SAME || echo DIFF)" > $L/v4-gate.txt
grep "не кратны" $L/missing-final.csv | cut -d, -f1,2 | tr , ' ' | xargs -P $P -L1 bash -c '/root/tk037-one.sh $0 $1' 2>/dev/null
touch $L/ALL_DONE_V4
