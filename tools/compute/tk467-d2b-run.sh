#!/bin/bash
# TK-467: D2b Санчо новым драйвером (один вызов ядра на пару). Те же 252 пары, 15 процессов x 1 поток; готовые пары (непустой файл в выходе) пропускаются.
# bash tk467-d2b-run.sh [список пар "SYM tf" файлом]   выход по умолчанию /data/tk0461/d2b-out (OUT= — другой каталог), метка DONE
# TK-469: умолчание — потоковое ядро tk469-pair-sim3 (STREAM=1; пик ARUSDT-60 636 МБ вместо 5,5 ГБ, BYTES_EQ с sim2 3/3), питон+ядро ≈1,03 ГБ/пару ⇒ 15 процессов ≈ 16 ГБ.
# Запуск заявкой: python3 /data/sched/alsched.py submit ... -- systemd-run-юнит с -p MemoryMax=24G; NX=15 (умолчание). BIN=…sim2 STREAM=0 — прежний режим.
set -u
W=/data/tk0461; O=${OUT:-$W/d2b-out}; PY=/data/tk421/venv/bin/python
export BIN=${BIN:-/data/tk0469/bin/tk469-pair-sim3} STREAM=${STREAM:-1} SRC=/srv/alpha-sda/candles-1m/binance-um EV=$W/d2b-ev CELLS=$W/d2b-cells FUND=/data/tk0455/fund TMP=$O/tmp O PY W
export PYTHONPATH=/data/tk0455:$W:/data/tk0467
mkdir -p $O $TMP; rm -f $O/DONE
if [ -n "${1:-}" ]; then cp "$1" $O/plan.txt; else ls $EV | grep -E '^[A-Z0-9]+-(60|240|1440)\.csv\.gz$' | sed 's/\.csv\.gz$//; s/-/ /' | sort -k2,2n -k1,1 > $O/plan.txt; fi
job() { o=$O/$1-$2.csv.gz; [ -s "$o" ] && return 0
  $PY ${DRV:-/data/tk0469/tk467_d2b_run_stream.py} $1 $2 $o.tmp 2>>$O/err.log && mv $o.tmp $o || echo "FAIL $1 $2" >>$O/err.log; }
export -f job
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < $O/plan.txt
echo "pairs $(wc -l <$O/plan.txt) done $(ls $O/*.csv.gz | wc -l) fail $(grep -c FAIL $O/err.log 2>/dev/null)" > $O/DONE
