#!/bin/bash
# TK-463: полный D2a ядром по всем парам /data/tk0461/ev (28 ячеек, 15 процессов x 1 поток, gz-1) + гейт «биты f64» каждой пары против /data/tk0461/scan.
# Свои выводы — /data/tk0463/d2a_full; итог d2a_full/FULL.DONE и full.log: стена счёта, строк, IDENTICAL/всего, пары без вывода.
set -u
B=/data/tk0463; DF=$B/d2a_full; BIN=$B/bin/tk463-sim; SRC=/srv/alpha-sda/candles-1m/binance-um; W=/data/tk0461; FUND=/data/tk0455/fund; PY=/data/tk421/venv/bin/python
mkdir -p $DF; rm -f $DF/FULL.DONE $DF/full.log $DF/*.csv.gz $DF/*.err $DF/*.cmp
ls $W/ev | grep '\.csv\.gz$' | sed 's/\.csv\.gz$//' | awk -F- '{print $1, $2}' > $DF/pairs.txt
job() { c=$1; tf=$2; $BIN $SRC/$c.csv.gz $tf $W/ev/$c-$tf.csv.gz $FUND $B/out/cells.csv $DF/$c-$tf.csv.gz --threads 1 --gz 1 2>$DF/$c-$tf.err; echo "rc $?" >>$DF/$c-$tf.err; }
cmpj() { c=$1; tf=$2; echo "$c-$tf $($PY $B/tk463_gate.py cmp $W/scan/$c-$tf.csv.gz $DF/$c-$tf.csv.gz 2>&1 | tail -2 | tr '\n' ' ')" >$DF/$c-$tf.cmp; }
export -f job cmpj; export B DF BIN SRC W FUND PY
export PYTHONPATH=/data/tk0455:$W:$B
s=$(date +%s.%N)
xargs -P 15 -L1 bash -c 'job $0 $1' < $DF/pairs.txt
e=$(date +%s.%N)
echo "пар $(wc -l <$DF/pairs.txt); стена счёта (15 проц. x 1 поток, gz-1, с загрузкой свечей на пару) $(echo "$e - $s" | bc) с; без вывода: $(for f in $DF/*.err; do grep -q 'rc 0' $f || echo -n "$(basename $f .err) "; done)" >>$DF/full.log
xargs -P 15 -L1 bash -c 'cmpj $0 $1' < $DF/pairs.txt
cat $DF/*.cmp >$DF/cmp.log
echo "cmp: IDENTICAL $(grep -c IDENTICAL $DF/cmp.log) из $(wc -l <$DF/pairs.txt); строк всего $(awk '/IDENTICAL/{for(i=1;i<=NF;i++) if($i=="IDENTICAL") s+=$(i+1)} END{print s}' $DF/cmp.log)" >>$DF/full.log
echo done >$DF/FULL.DONE
