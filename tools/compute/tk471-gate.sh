#!/bin/bash
# TK-471: гейт `--legs 1` ядра (столбцы bt, bm в режиме --pair) на готовых парах D2b Санчо: один прогон драйвера TK-467, обёртка
# вызывает новое ядро дважды — без --legs (выход драйвера = готовый d2b-out, BYTES_EQ) и с --legs 1 (<out>.legs: без двух
# последних столбцов == готовый выход байт в байт; в каждой строке bt <= b <= bm).
# bash tk471-gate.sh "SYM tf" ["SYM tf" ...]   (BIN=новое ядро; OUTREF=<каталог готовых выходов>); итог /data/tk0471/gate/DONE
set -u
W=/data/tk0461; G=${G:-/data/tk0471/gate}; REF=${OUTREF:-$W/d2b-out}; PY=/data/tk421/venv/bin/python
mkdir -p $G/tmp; rm -f $G/DONE
# вторая половина обёртки: второй вызов пишет в <out>.legs.csv.gz (подменяем 6-й аргумент)
cat >$G/wrap.sh <<EOW
#!/bin/bash
B=${BIN:-/data/tk0471/bin/tk471-pair-sim}
/usr/bin/time -f "%e с, пик RSS %M КБ" -o "\$6.tnew" \$B "\$@" 2>/dev/null || exit 1
a=("\$@"); l=\$6; a[5]="\${l%.csv.gz}.legs.csv.gz"
\$B "\${a[@]}" --legs 1 2>/dev/null || exit 1
b=("\$@"); b[5]="\${l%.csv.gz}.old.csv.gz"
${OLD:-/data/tk0469/bin/tk469-pair-sim3} "\${b[@]}" 2>/dev/null || exit 1
EOW
chmod +x $G/wrap.sh
export BIN=$G/wrap.sh STREAM=${STREAM:-1} SRC=/srv/alpha-sda/candles-1m/binance-um EV=$W/d2b-ev CELLS=${CELLS:-$W/d2b-cells} FUND=/data/tk0455/fund TMP=$G/tmp
export PYTHONPATH=/data/tk0455:$W:/data/tk0467
one() { sym=$1; tf=$2; o=$G/$sym-$tf.csv.gz; l=$G/tmp/$sym-$tf/out.legs.csv.gz; old=$G/tmp/$sym-$tf/out.old.csv.gz
  $PY /data/tk0467/tk467_d2b_run.py $sym $tf $o 2>$G/$sym-$tf.err
  t=$(cat $G/tmp/$sym-$tf/out.csv.gz.tnew 2>/dev/null); r=$REF/$sym-$tf.csv.gz
  if [ -s $r ]; then cmp <(zcat $o) <(zcat $r) >/dev/null && v=BYTES_EQ || v=DIFF; else v=NOREF; fi
  cmp <(zcat $o) <(zcat $old) >/dev/null && v="$v/OLDBIN_EQ" || v="$v/OLDBIN_DIFF"
  cmp <(zcat $l | awk -F, -v OFS=, '{NF-=2; print}') <(zcat $old) >/dev/null && w=LEGS_CUT_EQ || w=LEGS_CUT_DIFF
  q=$(zcat $l | awk -F, 'NR==1{for(i=1;i<=NF;i++)ix[$i]=i; next} {if($ix["bt"]>$ix["b"]+1e-6||$ix["b"]>$ix["bm"]+1e-6)bad++} END{print bad+0, NR-1}')
  echo "$sym-$tf default:$v legs:$w порядок_bt<=b<=bm(нарушений строк): $q | $t" >>$G/gate.log; }
export -f one; export G REF PY
: >$G/gate.log
printf '%s\n' "$@" | xargs -P "${NX:-3}" -L1 bash -c 'one $0 $1'
echo "pairs $# default_eq_oldbin $(grep -c 'OLDBIN_EQ' $G/gate.log) legs_cut_eq $(grep -c 'LEGS_CUT_EQ' $G/gate.log)" >$G/DONE
