#!/bin/bash
# TK-461 D2b S1-S5: events (tk461_s_stage.py -> d2b-ev-s) -> open k/M/v (tk461_open.py -> open-s, be-k-s.csv) -> ячейки (d2b-cells-s) -> D2b потоковым ядром (d2b-out-s); метки open-s/DONE, d2b-out-s/DONE
# Запуск заявкой: alsched submit --cls prod --name tk461-s-d2b ... -- bash /data/tk0461/tk461-s-run.sh   (MemoryMax 24G, NX по ядрам заявки). S6 (71 цепочка канала, tf=1) — отдельно.
set -u
W=/data/tk0461; EV=$W/d2b-ev-s; OUT=$W/open-s; PY=/data/tk421/venv/bin/python; SRC=/srv/alpha-sda/candles-1m/binance-um; NX=${NX:-12}
mkdir -p "$OUT"; rm -f "$OUT/DONE"
export PYTHONPATH=/data/tk0455:$W OUT PY SRC W EV
[ -d $EV ] && [ -n "$(ls $EV | head -1)" ] || $PY -I $W/tk461_s_stage.py $EV $W/bpev/out >>$OUT/log.txt 2>>$OUT/err.log || { echo "FAIL stage" >>$OUT/err.log; exit 1; }
job() { o=$OUT/$1-$2.csv.gz; [ -s "$o" ] && return 0; [ -s $EV/$1-$2.csv.gz ] || return 0
  $PY $W/tk461_open.py $SRC/$1.csv.gz $2 $EV/$1-$2.csv.gz /data/tk0455/fund $o.tmp >>$OUT/log.txt 2>>$OUT/err.log && mv $o.tmp $o || echo "FAIL $1 $2" >>$OUT/err.log; }
export -f job
ls $EV/*.csv.gz | sed 's|.*/||; s|\.csv\.gz||; s|-| |' > $OUT/plan.txt
xargs -P "$NX" -L1 bash -c 'job $0 $1' < $OUT/plan.txt
echo "jobs $(wc -l <$OUT/plan.txt) done $(ls $OUT/*.csv.gz | wc -l) fail $(grep -c FAIL $OUT/err.log 2>/dev/null)" > $OUT/DONE
grep -q FAIL $OUT/err.log 2>/dev/null && exit 1
$PY $W/tk461_open_q.py $OUT $W/be-k-s.csv >>$OUT/log.txt 2>&1 || exit 1
$PY $W/tk461_d2b_cells.py $W/be-k-s.csv $W/d2b-cells-s >>$OUT/log.txt 2>&1 || exit 1
EV=$EV CELLS=$W/d2b-cells-s OUT=$W/d2b-out-s NX=$NX bash /data/tk0469/tk461-d2b-run-bp.sh
