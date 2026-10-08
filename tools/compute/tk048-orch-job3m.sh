#!/bin/bash
# tk048-orch-job.sh <S> <R> <сутки> <k> <G>: группа k из G подряд идущих символов пула суток — сетка в своём cwd (тот же относительный путь study/root-<сутки>),
# затем, когда готовы все G групп суток, склейка и хвост скрипта суток (awk-нарезка). Вызывается из tk048-orch-grp.sh.
S=$1; R=$2; d=$3; k=$4; G=$5; MON=${MON:-jan}; H=/data/tk046/$MON/home; E=$H/alpha/epochs/e-$MON
J=$H/alpha/tmp-p07/cells-by-day/jall-$MON-$d.sh; W=$S/g/$d-$k
export HOME=$H; export ALPHA_E2E=$R/e2e-$d-$k.jsonl
mkdir -p $W/study $W/b5 $R
for x in $S/* $S/.[!.]*; do b=$(basename $x); case $b in study|b5|g|.day-*) continue;; esac; [ -e "$x" ] && ln -s $(readlink -f $x) $W/$b; done
for x in $S/study/*; do b=$(basename $x); case $b in root-*) continue;; esac; ln -s $(readlink -f $x) $W/study/$b; done
mkdir -p $W/study/root-$d; cp -rs $(readlink -f $S/study/root-$d)/. $W/study/root-$d/; rm -f $W/study/root-$d/instruments.csv
src=$(readlink -f $S/study/root-$d)/instruments.csv
python3 - "$src" "$W/study/root-$d/instruments.csv" $k $G <<'PY'
import sys
src,dst,k,G=sys.argv[1],sys.argv[2],int(sys.argv[3]),int(sys.argv[4])
L=open(src,newline='').read().split('\n')
hdr=L[0]; rows=[l for l in L[1:] if l]
n=len(rows); a=k*n//G; b=(k+1)*n//G
open(dst,'w',newline='').write('\n'.join([hdr]+rows[a:b])+'\n')
PY
cd $W || exit 2
sed -n 1,3p $J | sed "${EVENTS_WIDE:+s# bounce-grid # bounce-grid --events wide #}" > $W/A.sh
s0=$(date +%s.%N); /usr/bin/time -f "%e %U %S %M" -o $R/t-$d-$k.txt bash $W/A.sh > $R/g-$d-$k.out 2> $R/g-$d-$k.err || { echo "$d $k FAIL" >> $R/fail.txt; exit 1; }
echo "$d $k $s0 $(date +%s.%N)" >> $R/units.txt; touch $S/done-$d-$k
[ "$(ls $S/done-$d-* | wc -l)" -eq "$G" ] || exit 0
mkdir $S/merge-$d 2>/dev/null || exit 0
tm0=$(date +%s.%N)
python3 - $S $d $G <<'PY'
import sys,os
S,d,G=sys.argv[1],sys.argv[2],int(sys.argv[3])
for nm in (".cellstmp-",".m2tmp-",".m3tmp-"):
    rels=set()
    for k in range(G):
        base=f"{S}/g/{d}-{k}/b5/{nm}{d}"
        for r,_,fs in os.walk(base):
            for f in fs: rels.add(os.path.relpath(os.path.join(r,f),base))
    for rel in sorted(rels):
        out=f"{S}/b5/{nm}{d}/{rel}"; os.makedirs(os.path.dirname(out),exist_ok=True)
        first=True
        with open(out,'wb') as o:
            for k in range(G):
                p=f"{S}/g/{d}-{k}/b5/{nm}{d}/{rel}"
                if not os.path.exists(p): continue
                lines=open(p,'rb').read().split(b'\n')
                o.write(b'\n'.join(lines) if first else b'\n'.join(lines[2:]))
                first=False
PY
tm1=$(date +%s.%N)
cd $S && sed -n '5,$p' $J > $S/B-$d.sh && /usr/bin/time -f "%e %U %S %M" -o $R/tb-$d.txt bash $S/B-$d.sh > $R/b-$d.out 2> $R/b-$d.err || echo "$d B FAIL" >> $R/fail.txt
rm -rf $S/b5/.cellstmp-$d
echo "$d $(date +%s.%N)" >> $R/finish.txt
echo "$d merge_s $(echo "$tm1 - $tm0" | bc)" >> $R/merge.txt
