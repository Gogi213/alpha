#!/bin/bash
# TK-463: синтетика P-TK461 §9 п.2 — ядро tk463-sim против эталонов (D2a: tk461_scan.run, D2b: tk463_d2b_ref) на рукотворной серии, ТФ 5/15/60.
# bash tk463-synth-gate.sh; итог /data/tk0463/synth/SYNTH.DONE: «jobs N ok M»; свои выводы только в /data/tk0463/synth
set -u
B=/data/tk0463; OUT=$B/synth; PY=/data/tk421/venv/bin/python; BIN=$B/bin/tk463-sim; T=$B/tk463_gate.py
mkdir -p "$OUT"; rm -f "$OUT/SYNTH.DONE" "$OUT/gate.log"
export PYTHONPATH=/data/tk0455:/data/tk0461:$B
cp /opt/rbuild/target-slot1/release/tk463-sim "$BIN" 2>/dev/null || true
$PY $B/tk463_gate.py cells $OUT/cells-d2a.csv; $PY $B/tk463_synth.py cells $OUT/cells-d2b.csv
for tf in 5 15 60; do
  $PY $B/tk463_synth.py gen $OUT $tf
  $PY $B/tk463_synth.py ref-d2a $OUT $tf 2>>$OUT/err.log
  $BIN $OUT/SYNTHUSDT.csv.gz $tf $OUT/ev-$tf.csv $OUT/fund $OUT/cells-d2a.csv $OUT/rs-d2a-$tf.csv.gz --threads 1 2>>$OUT/err.log
  echo "d2a $tf :: $($PY $T cmp $OUT/ref-d2a-$tf.csv.gz $OUT/rs-d2a-$tf.csv.gz 2>&1 | tail -3 | tr '\n' ' ')" >>$OUT/gate.log
  $PY $B/tk463_d2b_ref.py ref $OUT/SYNTHUSDT.csv.gz $tf $OUT/in-$tf.csv $OUT/fund $OUT/cells-d2b.csv $OUT/ref-d2b-$tf.csv.gz 2>>$OUT/err.log
  $BIN $OUT/SYNTHUSDT.csv.gz $tf $OUT/in-$tf.csv $OUT/fund $OUT/cells-d2b.csv $OUT/rs-d2b-$tf.csv.gz --threads 1 2>>$OUT/err.log
  echo "d2b $tf :: $($PY $T cmp $OUT/ref-d2b-$tf.csv.gz $OUT/rs-d2b-$tf.csv.gz 2>&1 | tail -3 | tr '\n' ' ')" >>$OUT/gate.log
  $PY $B/tk463_synth.py cover $OUT/ref-d2b-$tf.csv.gz >$OUT/cover-d2b-$tf.txt 2>&1
  $PY $B/tk463_synth.py cover $OUT/ref-d2a-$tf.csv.gz >$OUT/cover-d2a-$tf.txt 2>&1
done
echo "jobs 6 ok $(grep -c IDENTICAL $OUT/gate.log) lines $(wc -l <$OUT/gate.log)" > $OUT/SYNTH.DONE
