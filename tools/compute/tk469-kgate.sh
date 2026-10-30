#!/bin/bash
# TK-469: гейт потокового режима ядра (--stream 1) на готовых парах bp3: драйвер TK-467 с обёрткой вместо ядра, выход сравнивается
# с готовым выходом прежнего ядра (tk467-pair-sim2) после gunzip; в лог — время и пик RSS нового ядра.
# bash tk469-kgate.sh "SYM tf" ["SYM tf" ...]   (OUTREF=<каталог готовых выходов>, по умолчанию d2b-out-bp3); итог /data/tk0469/kgate/DONE
set -u
W=/data/tk0461; G=/data/tk0469/kgate; REF=${OUTREF:-$W/d2b-out-bp3}; PY=/data/tk421/venv/bin/python
mkdir -p $G/tmp; rm -f $G/DONE
cat >$G/wrap.sh <<'EOW'
#!/bin/bash
/usr/bin/time -f "%e с, пик RSS %M КБ" -o "$6.tnew" /data/tk0469/bin/tk469-pair-sim3 "$@" --stream 1 2>/dev/null
EOW
chmod +x $G/wrap.sh
export BIN=$G/wrap.sh SRC=/srv/alpha-sda/candles-1m/binance-um EV=$W/d2b-ev-bp3 CELLS=$W/d2b-cells-bp3 FUND=/data/tk0455/fund TMP=$G/tmp
export PYTHONPATH=/data/tk0455:$W:/data/tk0467
one() { sym=$1; tf=$2; o=$G/$sym-$tf.csv.gz
  $PY /data/tk0467/tk467_d2b_run.py $sym $tf $o 2>$G/$sym-$tf.err
  t=$(cat $G/tmp/$sym-$tf/out.csv.gz.tnew 2>/dev/null); r=$REF/$sym-$tf.csv.gz
  if [ -s $r ]; then cmp <(zcat $o) <(zcat $r) >/dev/null && v=BYTES_EQ || v=DIFF; else v=NOREF; fi
  echo "$sym-$tf $v | stream: $t | строк $(zcat $o | wc -l)" >>$G/gate.log; }
export -f one; export G REF PY
: >$G/gate.log
printf '%s\n' "$@" | xargs -P "${NX:-4}" -L1 bash -c 'one $0 $1'
echo "pairs $# ok $(grep -c BYTES_EQ $G/gate.log) diff $(grep -c DIFF $G/gate.log)" >$G/DONE
