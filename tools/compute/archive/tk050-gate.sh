#!/bin/bash
# tk050-gate.sh <БИН> [СИМВОЛ]: ATOM 01-15, два прогона одним бинарником под замком /data/tk-bench.lock:
# ev=0 — ALPHA_EVENT_STEPS=0 (до), ev=1 — =1 (после); оба сверяются diff -r с /data/tk049/<SYM>-base/cells.
# Рабочий каталог свой (/data/tk050/w) — не пересекается с гейтами TK-049. Итог: /data/tk050/<SYM>-<БИН>/{metrics.txt,gate.txt,grid*.log,.gate}.
BIN=${1:?бинарник}; SYM=${2:-ATOMUSDT}; H=/data/tk046/jan/home; W=/data/tk050/w; R=/data/tk050/$SYM-$BIN
mkdir -p $R; ln -sfn /opt/alpha-compute/bin/$BIN $W/bin/alpha-tk044k1-new
exec 9>/data/tk-bench.lock; flock -w 7200 9
export HOME=$H ALPHA_ATTEMPT_STATS=1; cd $W || exit 2
for v in 0 1; do
  rm -rf $W/b5/.cellstmp-$SYM
  sed -n 3p $H/alpha/tmp-p07/cells-by-day/jall-jan-2026-01-15.sh | sed "s# lob bounce-grid # lob bounce-grid --symbol $SYM #; s#b5/.cellstmp-2026-01-15.log#$R/grid$v.log#; s#b5/.cellstmp-2026-01-15#b5/.cellstmp-$SYM#; s#--extra-runs [^ ]*##" > $R/cmd$v.sh
  s=$(date +%s.%N); ALPHA_EVENT_STEPS=$v bash $R/cmd$v.sh > $R/run$v.out 2> $R/run$v.err; e=$(date +%s.%N)
  echo "ev=$v wall_s $(echo "$e - $s" | bc)" >> $R/metrics.txt
  diff -r /data/tk049/$SYM-base/cells $W/b5/.cellstmp-$SYM > $R/diff$v.txt 2>&1; echo "ev=$v diff_rc $?" >> $R/gate.txt
done
touch $R/.gate
