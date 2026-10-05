#!/bin/bash
# TK-031: январь 31 суток merge-v3 при N полосах; холодный кэш перед каждым N. tk031-month.sh "8 12 16"
export HOME=/home/deck; A=$HOME/alpha; E=$A/epochs/e-jan; S=/data/alpha/epochs/e-jan; O=$A/tk031; mkdir -p $O; cd $E
for d in $(seq -w 1 31); do V=$E/study/root-2026-01-$d; mkdir -p $V; for f in $S/root/*-2026-01-$d.binlog; do ln -sf $f $V/; done; cp -n $S/root/*-2026-01-$d.binlog.events $V/ 2>/dev/null; cp -n $S/root/verify-*.status $S/root/session.json $S/root/instruments.csv $V/; done
python3 $A/bin/p07-all-month.py jan --merge --bin alpha-tk029-merge-v3 > $O/gen.out 2>&1
: > $O/summary.txt
for N in $1; do
  rm -rf $E/b5/*; sync; echo 3 > /proc/sys/vm/drop_caches
  vmstat 5 > $O/vm$N.txt & VM=$!
  t0=$(date +%s)
  K=${K:-8}
  for d in $(seq -w 1 $N); do cat $S/root/*-2026-01-$d.binlog* > /dev/null; done
  ( for d in $(seq -w $((N+1)) 31); do while [ $(( $(ls $O/L$N-*.rc 2>/dev/null | wc -l) + N + K )) -lt $((10#$d)) ]; do sleep 2; done; cat $S/root/*-2026-01-$d.binlog* > /dev/null; done ) & PF=$!
  seq -w 1 31 | xargs -P $N -I{} bash -c "bash $A/tmp-p07/cells-by-day/jall-jan-2026-01-{}.sh > $O/L$N-{}.out 2> $O/L$N-{}.err; echo \$? > $O/L$N-{}.rc"
  t1=$(date +%s); kill $VM $PF 2>/dev/null
  rcs=$(cat $O/L$N-*.rc | sort | uniq -c | tr '\n' ' ')
  awk -v n=$N -v w=$((t1-t0)) -v r="$rcs" 'NR>2{u+=$13;s+=$14;i+=$15;wa+=$16;c++;if($4<mn||mn=="")mn=$4} END{printf "N=%d wall=%ds us=%.0f sy=%.0f id=%.0f wa=%.1f minfree_MB=%d rc: %s\n",n,w,u/c,s/c,i/c,wa/c,mn/1024,r}' $O/vm$N.txt >> $O/summary.txt
  du -sb $E/b5 | cut -f1 > $O/b5size$N
  ( cd $E; find b5 -type f ! -name "*.log" | LC_ALL=C sort | xargs -P 8 -n 50 sha256sum > /dev/null )
  if [ "$N" = "$(echo $1 | awk '{print $1}')" ]; then ( cd $E; find b5 -type f ! -name "*.log" | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(grep -v '^#' "$f" | sha256sum | cut -d' ' -f1)" "$f"; done > $O/body-first.sha ); else ( cd $E; find b5 -type f ! -name "*.log" | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(grep -v '^#' "$f" | sha256sum | cut -d' ' -f1)" "$f"; done > $O/body-$N.sha ); cmp $O/body-first.sha $O/body-$N.sha && echo "N=$N sha==first" >> $O/summary.txt || echo "N=$N sha DIFF" >> $O/summary.txt; fi
done
date > $O/DONE
