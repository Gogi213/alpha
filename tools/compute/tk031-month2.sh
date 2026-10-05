#!/bin/bash
# TK-031: январь 31 суток merge-v3. Фаза A: весь root в page cache одним последовательным читателем; фаза B: счёт при N полосах из кэша.
export HOME=/home/deck; A=$HOME/alpha; E=$A/epochs/e-jan; S=/data/alpha/epochs/e-jan; O=$A/tk031; mkdir -p $O; cd $E
: > $O/summary.txt
rm -rf $E/b5/.m[23]tmp* $O/L* $O/DONE
sync; echo 3 > /proc/sys/vm/drop_caches
for N in $1; do
  rm -rf $E/b5/*; rm -f $O/L$N-* $O/cached-*; sync; echo 3 > /proc/sys/vm/drop_caches
  W=${W:-10}
  find -L $E/study/sigma240 $E/study/klines $E/study/regime -type f | xargs -r cat > /dev/null
  ( for d in $(seq -w 1 31); do while [ $(( $(ls $O/L$N-*.rc 2>/dev/null | wc -l) + W )) -lt $((10#$d)) ]; do sleep 1; done; cat $E/study/root-2026-01-$d/* > /dev/null; find -L $E/study/approaches/D20/2026-01-$d -type f | xargs -r cat > /dev/null; touch $O/cached-$d; done ) & PF=$!
  vmstat 5 > $O/vm$N.txt & VM=$!
  t0=$(date +%s)
  seq -w 1 31 | xargs -P $N -I{} bash -c "while [ ! -e $O/cached-{} ]; do sleep 1; done; bash $A/tmp-p07/cells-by-day/jall-jan-2026-01-{}.sh > $O/L$N-{}.out 2> $O/L$N-{}.err; echo \$? > $O/L$N-{}.rc"
  t1=$(date +%s); kill $VM $PF 2>/dev/null
  rcs=$(cat $O/L$N-*.rc | sort | uniq -c | tr '\n' ' ')
  awk -v n=$N -v w=$((t1-t0)) -v r="$rcs" 'NR>2{u+=$13;s+=$14;i+=$15;wa+=$16;bi+=$9;c++} END{printf "B N=%d wall=%ds us=%.0f sy=%.0f id=%.0f wa=%.1f bi_KBs=%d rc: %s\n",n,w,u/c,s/c,i/c,wa/c,bi/c,r}' $O/vm$N.txt >> $O/summary.txt
  ( cd $E; find b5 -type f ! -name "*.log" | LC_ALL=C sort | while read -r f; do printf '%s  %s\n' "$(grep -v '^#' "$f" | sha256sum | cut -d' ' -f1)" "$f"; done > $O/body-$N.sha )
  [ "$N" != "${1%% *}" ] && { cmp $O/body-${1%% *}.sha $O/body-$N.sha && echo "N=$N sha==first" >> $O/summary.txt || echo "N=$N sha DIFF" >> $O/summary.txt; }
done
date > $O/DONE
