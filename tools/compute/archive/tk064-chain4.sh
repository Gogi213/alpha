#!/bin/bash
# perf stat head/r1d (3 пары) -> pf5/res.txt, затем speed.sh head vs r1d 10 повторов -> sp4/res.txt
R=/data/tk064/g2/root-SUIUSDT-2026-09-20; OUT=/data/tk064/pf5; mkdir -p $OUT; : > $OUT/res.txt
FL="--h3-mode notional --h3-usd 10000 --approach-bps 20"
for i in 1 2 3; do for v in head r1d; do mkdir -p $OUT/$v-$i/mf
 perf stat -x, -e instructions,cycles,branch-misses,L1-icache-load-misses -o $OUT/$v-$i/perf.txt nice -n 5 /opt/alpha-compute/bin/b17$v lob touches --root $R --symbol SUIUSDT $FL --minute-flow $OUT/$v-$i/mf --out $OUT/$v-$i/t.csv > /dev/null 2>&1
 echo "$v $i $(grep -v "^#" $OUT/$v-$i/perf.txt | awk -F, '{printf "%s=%s ", $3, $1}')" >> $OUT/res.txt; done; done; echo done >> $OUT/res.txt
bash /data/tk064/speed.sh /opt/alpha-compute/bin/b17head /opt/alpha-compute/bin/b17r1d /data/tk064/sp4 10
