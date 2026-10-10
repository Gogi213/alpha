#!/bin/bash
# tk048-io.sh <сутки> [бинарник]: сутки января заданием эталона с холодным кэшем: стена, user, байт прочитано с sdb (diskstats), гейт против b5 эталона. Выход /data/tk048/io-<сутки>-<bin>/metrics.txt
d=${1:-2026-01-01}; bin=${2:-alpha-tk048b-v3}; MON=jan
H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON; S=/data/tk048/io-$d-$bin; R=$S.out
rm -rf $S $R; mkdir -p $S/b5 $R
for x in $E/* $E/.[!.]*; do b=$(basename $x); case $b in b5|b5-solo|.day-*) continue;; esac; [ -e "$x" ] && ln -s $x $S/$b; done
rm -f $S/bin; mkdir $S/bin; ln -s /opt/alpha-compute/bin/$bin $S/bin/alpha-tk044k1-new
export HOME=$H; cd $S || exit 2
sync; echo 3 > /proc/sys/vm/drop_caches
rd0=$(awk '$3=="sdb"{print $6, $4}' /proc/diskstats); s=$(date +%s.%N)
bash -c "time bash $H/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh" > $R/run.out 2> $R/run.err
e=$(date +%s.%N); rd1=$(awk '$3=="sdb"{print $6, $4}' /proc/diskstats)
echo "wall_s $(echo "$e - $s" | bc)" > $R/metrics.txt; grep -E "^(real|user|sys)" $R/run.err >> $R/metrics.txt
echo "sdb sectors+reqs before: $rd0 after: $rd1" >> $R/metrics.txt
python3 -c "a='$rd0'.split();b='$rd1'.split();print('read_MB',(int(b[0])-int(a[0]))*512/1e6,'reqs',int(b[1])-int(a[1]))" >> $R/metrics.txt
bad=0; n=0; cd $S/b5; while IFS= read -r f; do n=$((n+1)); cmp -s "$f" "$E/b5/$f" || bad=$((bad+1)); done < <(find . -type f -path "*$d*" | grep -v "/\.")
echo "gate files $n diff $bad" >> $R/metrics.txt; touch $R/.done
