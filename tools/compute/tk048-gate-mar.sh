#!/bin/bash
# TK-048 гейт вне янв+фев (07.10): суток марта × 4 монеты тремя способами — REF (alpha-b15pyr4pgoflag, без кэша), W (alpha-b16tpgoflag, строит .abin на диске), G (то же, кэш из /dev/shm только на чтение).
# Запуск: systemd-run --unit tk048-gatemar --collect /data/tk052/benchrun2.sh stand bash /data/tk048/gate-mar.sh ; выход /data/tk048/gate-mar.txt, маркер gate-mar.done.
DAYS="2026-03-01 2026-03-02 2026-03-03"; SYM="--symbol ADAUSDT --symbol HYPEUSDT --symbol LTCUSDT --symbol NEARUSDT"
G=/data/tk048/gmar; OUT=/data/tk048/gate-mar.txt; AB=/data/tk048/abin-mar; MON=mar; H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON
rm -f /data/tk048/gate-mar.done $OUT; rm -rf $G $AB; mkdir -p $G
run() { v=$1; bin=$2; abdir=$3; d=$4; S=$G/$v-$d
  mkdir -p $S/b5 $S/bin; ln -s /opt/alpha-compute/bin/$bin $S/bin/alpha-tk044k1-new
  for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|b5-ref|bin|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
  grep -v '^cp b5/.cellstmp-.*[.]log /data' /data/tk065/days/r2-$d.sh | sed "s# --cells # $SYM --cells #" > $S/day.sh
  ( export HOME=$H ALPHA_SKIP_SAME=1; [ -n "$abdir" ] && export ALPHA_APPROACH_BIN_DIR=$abdir; cd $S; t0=$(date +%s); bash $S/day.sh > run.out 2> run.err; echo "$v $d rc $? wall_s $(( $(date +%s)-t0 ))" >> $OUT ) ; }
for d in $DAYS; do run REF alpha-b15pyr4pgoflag "" $d; done
for d in $DAYS; do run W alpha-b16tpgoflag $AB $d; done
n_ab=$(find $AB -name '*.abin' | wc -l); echo "abin_built $n_ab" >> $OUT
bash /data/tk048/abin-shm.sh up $AB gate $DAYS >> $OUT 2>&1
before=$(find /dev/shm/abin-gate -type f | wc -l)
for d in $DAYS; do run G alpha-b16tpgoflag /dev/shm/abin-gate $d; done
echo "shm_files_before $before after $(find /dev/shm/abin-gate -type f | wc -l)" >> $OUT
bash /data/tk048/abin-shm.sh down gate
for d in $DAYS; do for v in W G; do echo "diff REF-$v $d: $(diff -rq $G/REF-$d/b5 $G/$v-$d/b5 | wc -l) files_ref $(find $G/REF-$d/b5 -type f | wc -l)" >> $OUT; done; done
touch /data/tk048/gate-mar.done
