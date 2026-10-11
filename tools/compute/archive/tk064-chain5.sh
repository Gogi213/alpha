#!/bin/bash
# perf record (инструкции) r1e -> pf6/r1e/top.txt; perf stat head/r1e 3 пары -> pf7/res.txt
bash /data/tk064/prof2.sh r1e
R=/data/tk064/g2/root-SUIUSDT-2026-09-20; OUT=/data/tk064/pf7; mkdir -p $OUT; : > $OUT/res.txt
FL="--h3-mode notional --h3-usd 10000 --approach-bps 20"
for i in 1 2 3; do for v in head r1e; do mkdir -p $OUT/$v-$i/mf
 perf stat -x, -e instructions,cycles,branch-misses,L1-icache-load-misses -o $OUT/$v-$i/perf.txt nice -n 5 /opt/alpha-compute/bin/b17$v lob touches --root $R --symbol SUIUSDT $FL --minute-flow $OUT/$v-$i/mf --out $OUT/$v-$i/t.csv > /dev/null 2>&1
 echo "$v $i $(grep -v "^#" $OUT/$v-$i/perf.txt | awk -F, '{printf "%s=%s ", $3, $1}')" >> $OUT/res.txt; done; done; echo done >> $OUT/res.txt
