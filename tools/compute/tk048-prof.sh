#!/bin/bash
# tk048-prof.sh <сутки> [бинарник]: сутки января эталонным заданием в песочнице /data/tk048/<сутки>-<bin> (b5 пустой, остальное — ссылки), perf -F 249, сверка с b5 эталона.
d=${1:-2026-01-01}; bin=${2:-alpha-tk044k1-new}; MON=jan
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; S=/data/tk048/$d-$bin; R=/data/tk048/out-$d-$bin
rm -rf $S; mkdir -p $S/b5 $R
for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
ln -sfn /opt/alpha-compute/bin $S/bin-real; rm -f $S/bin; mkdir $S/bin; ln -s /opt/alpha-compute/bin/$bin $S/bin/alpha-tk044k1-new
export HOME=$H; cd $S || exit 2
s=$(date +%s.%N)
perf record -F 249 -o $R/perf.data -- bash -c "time bash $H/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh" > $R/run.out 2> $R/run.err
e=$(date +%s.%N); echo "wall_s $(echo "$e - $s" | bc)" > $R/metrics.txt; grep -E "^(real|user|sys)" $R/run.err >> $R/metrics.txt
perf report -i $R/perf.data --no-children --sort symbol --stdio 2>/dev/null | grep -v "^#" | grep -v "^$" | head -45 > $R/perf-top.txt
bad=0; n=0; cd $S/b5; while IFS= read -r f; do n=$((n+1)); cmp -s "$f" "$E/b5/$f" || { bad=$((bad+1)); echo "DIFF $f" >> $R/gate.txt; }; done < <(find . -type f -path "*$d*" | grep -v "/\.")
echo "gate files $n diff $bad" >> $R/metrics.txt; touch $R/.done
