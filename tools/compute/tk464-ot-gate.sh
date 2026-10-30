#!/bin/bash
# TK-464 (2): гейт «байт в байт» правки ОТ (деление % на каждой минуте -> граница окна): старый tk464-pair-sim против tk464-pair-sim2.
# TWTUSDT, cells449, ТФ 60/240/1440: (а) построчный режим --max-events 300; (б) --pair на всех событиях; время ТФ 1440 на 1 потоке.
# bash tk464-ot-gate.sh ; итог /data/tk0464/otgate/OT.DONE, gate.log
set -u
c=TWTUSDT; O=/data/tk0464/otgate; OLD=/data/tk0464/bin/tk464-pair-sim; NEW=/data/tk0464/bin/tk464-pair-sim2
SRC=/srv/alpha-sda/candles-1m/binance-um; FUND=/data/tk0455/fund; W=/data/tk0461; B=/data/tk0463; CELLS=/data/tk0464/bench/cells449.csv
mkdir -p $O; rm -f $O/OT.DONE $O/gate.log; cd $O
for tf in 60 240 1440; do
  zcat $W/ev/$c-$tf.csv.gz >$O/ev$tf.csv; ct=$B/out/$c-$tf.ctrl-in.csv
  for v in old new; do bin=$OLD; [ $v = new ] && bin=$NEW
    $bin $SRC/$c.csv.gz $tf $O/ev$tf.csv $FUND $CELLS $O/row-$tf-$v.csv --threads 4 --max-events 300 >$O/row-$tf-$v.log 2>&1
  done
  cmp $O/row-$tf-old.csv $O/row-$tf-new.csv && echo "row ТФ$tf BYTES_EQ $(wc -l <$O/row-$tf-old.csv) строк" >>$O/gate.log || echo "row ТФ$tf DIFF" >>$O/gate.log
  for v in old new; do bin=$OLD; [ $v = new ] && bin=$NEW
    th=4; [ $tf = 1440 ] && th=1
    /usr/bin/time -f "стена %e с, ЦП %U+%S с" $bin $SRC/$c.csv.gz $tf $O/ev$tf.csv $FUND $CELLS $O/pair-$tf-$v.csv --threads $th --pair $ct >$O/pair-$tf-$v.log 2>&1
    echo "pair ТФ$tf $v потоков $th: $(grep -E 'время|стена' $O/pair-$tf-$v.log | tr '\n' ' ')" >>$O/gate.log
  done
  cmp $O/pair-$tf-old.csv $O/pair-$tf-new.csv && echo "pair ТФ$tf BYTES_EQ $(wc -l <$O/pair-$tf-old.csv) строк" >>$O/gate.log || echo "pair ТФ$tf DIFF" >>$O/gate.log
  rm -f $O/ev$tf.csv $O/row-$tf-*.csv $O/pair-$tf-*.csv
done
echo "ok $(grep -c BYTES_EQ $O/gate.log) of 6, diff $(grep -c DIFF $O/gate.log)" >$O/OT.DONE
