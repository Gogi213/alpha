#!/bin/bash
# TK-470: контроли C1/C2 Санчо (A1/A4–A8) на потоковом ядре (tk469-pair-sim3 --stream 1), по паре (монета, ТФ), 15 процессов x 1 поток.
# bash tk470-c12-run.sh [список пар "SYM tf" файлом]   выход OUT (по умолчанию /data/tk0461/c12-out/{c1,c2}/<SYM>-<tf>.csv.gz), метка OUT/DONE
# CTL=CX — гейт: прежние контроли C-X тем же путём в OUT/cx/ (сравнить с d2b-out байт в байт).
set -u
W=/data/tk0461; O=${OUT:-$W/c12-out}; PY=/data/tk421/venv/bin/python
export BIN=${BIN:-/data/tk0469/bin/tk469-pair-sim3} STREAM=1 SRC=/srv/alpha-sda/candles-1m/binance-um EV=$W/d2b-ev CELLS=$W/d2b-cells FUND=/data/tk0455/fund TMP=$O/tmp O PY W
export PYTHONPATH=/data/tk0455:$W:/data/tk0467:/data/tk0470
R=cx; [ "${CTL:-}" = CX ] || R=c2
mkdir -p $O $TMP; rm -f $O/DONE
if [ -n "${1:-}" ]; then cp "$1" $O/plan.txt; else ls $EV | grep -E '^[A-Z0-9]+-(60|240|1440)\.csv\.gz$' | sed 's/\.csv\.gz$//; s/-/ /' | sort -k2,2n -k1,1 > $O/plan.txt; fi
job() { [ -s "$O/$R/$1-$2.csv.gz" ] && return 0   # готовая пара (последний из файлов пары) пропускается
  $PY /data/tk0470/tk470_c12_run.py $1 $2 $O 2>>$O/err.log || echo "FAIL $1 $2" >>$O/err.log; }
export -f job; export R
xargs -P "${NX:-15}" -L1 bash -c 'job $0 $1' < $O/plan.txt
echo "pairs $(wc -l <$O/plan.txt) done $(ls $O/$R/*.csv.gz | wc -l) fail $(grep -c FAIL $O/err.log 2>/dev/null)" > $O/DONE
