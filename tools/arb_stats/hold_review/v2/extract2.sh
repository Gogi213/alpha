#!/bin/bash
# review-2: extend /root/rev534/raw to the v6 window (12.11..16.11 UTC dirs); re-extract files whose source changed after the old extraction
set -u
OUT=/root/rev534/raw
declare -A ROOT=([binance]=/data/collect/binance/data [bybit]=/data/turbo-probe/soak-bybit/data [okx]=/data/collect/okx/data [bingx]=/data/collect/bingx/data [gate]=/data/collect/gate/data)
while read ex list; do
  for d in 2026-11-12 2026-11-13 2026-11-14 2026-11-15 2026-11-16; do
    for f in ${ROOT[$ex]}/$d/book*.tsv.zst ${ROOT[$ex]}/$d/trade*.tsv.zst; do
      [ -e "$f" ] || continue
      o=$OUT/$ex.$d.$(basename $f .tsv.zst).tsv
      if [ -e "$o" ] && [ "$o" -nt "$f" ]; then continue; fi
      echo "$ex $list $f"
    done
  done
done < /root/rev534/symlist.txt > /root/rev534/jobs2.txt
wc -l /root/rev534/jobs2.txt
cat /root/rev534/jobs2.txt | xargs -P 4 -L 1 bash -c '
ex=$0; list=$1; f=$2; o=/root/rev534/raw/$ex.$(basename $(dirname $f)).$(basename $f .tsv.zst).tsv
zstd -dc "$f" 2>/dev/null | mawk -F"\t" -v L="$list" "BEGIN{n=split(L,a,\",\");for(i=1;i<=n;i++)S[a[i]]=1}
NR==1{hi=substr(\$2,1,length(\$2)-12)+0; cum=substr(\$2,length(\$2)-11)+0; next_ok=1}
NR>1{cum+=\$2}
(\$1==\"b\"||\$1==\"t\") && (\$5 in S){rx=hi*1000000000+int(cum/1000000); printf \"%s\t%s\t%d\t%d\t%s\t%s\t%s\t%s\n\",\$1,\$5,rx-\$3,rx,\$7,\$8,\$9,\$10}" > $o.tmp && mv $o.tmp $o'
ls $OUT | wc -l
touch /root/rev534/EXTRACT2.DONE
