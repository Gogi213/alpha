#!/bin/bash
# gridperf.sh <A> <B> <OUT> <REPS> <d01|d15>: как gridspeed.sh, но perf stat (инструкции/циклы/branch-miss) вместо user-секунд; A/B чередуются; diff .cellstmp A vs B
A=$1; B=$2; OUT=$3; REPS=$4; M=$5; ST=/dev/shm/alpha-stand; H=/data/tk046/jan/home
case $M in d15) D=2026-01-15;; d01) D=2026-01-01;; *) exit 2;; esac
J=$H/alpha/tmp-p07/cells-by-day/jall-jan-$D.sh; mkdir -p $OUT; : > $OUT/res.txt
one() {
  W=/dev/shm/alpha-run/t64p-$M-$2-$3; rm -rf $W; mkdir -p $W/b5 $W/bin; ln -s $ST/study $W/study; ln -s $ST/root $W/root; ln -s /opt/alpha-compute/bin/$1 $W/bin/alpha-tk044k1-new
  sed -n 1,3p $J | sed "s#b5/.cellstmp-$D.log#$OUT/grid-$2-$3.log#" | sed "s#--busy-skip off#--busy-skip on --round-memo off#; s#--exit-group on#--exit-group off#" > $W/run.sh
  ( cd $W && export HOME=$H && nice -n 5 perf stat -x, -e instructions,cycles,branch-misses -o $OUT/perf-$2-$3.txt bash $W/run.sh > $OUT/run-$2-$3.out 2> $OUT/run-$2-$3.err )
  echo "$2 $3 $(grep -v '^#' $OUT/perf-$2-$3.txt | awk -F, '{printf "%s=%s ", $3, $1}')" >> $OUT/res.txt
  if [ "$3" = 1 ]; then rm -rf $OUT/cells-$2; cp -a $W/b5/.cellstmp-$D $OUT/cells-$2; fi; rm -rf $W
}
for i in $(seq $REPS); do if [ $((i%2)) = 1 ]; then one $A A $i; one $B B $i; else one $B B $i; one $A A $i; fi; done
diff -rq $OUT/cells-A $OUT/cells-B > $OUT/diff.txt 2>&1; echo "gate diff_rc $? files $(find $OUT/cells-A -type f | wc -l)" >> $OUT/res.txt; echo done >> $OUT/res.txt
