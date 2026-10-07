#!/bin/bash
# chain7 (TK-064 слияние R1 на голову 4b3f774c): perf stat touches SUI 20.09, 3 пары b20head/b20r1 чередуя -> pf10/res.txt
cd /data/tk064; rm -f chain7.done
OUT=/data/tk064/pf10; rm -rf $OUT; mkdir -p $OUT; : > $OUT/res.txt
cat > /data/tk064/perf10.sh <<'IN'
R=/data/tk064/g2/root-SUIUSDT-2026-09-20; OUT=/data/tk064/pf10
FL="--h3-mode notional --h3-usd 10000 --approach-bps 20"
for i in 1 2 3; do for v in head r1; do mkdir -p $OUT/$v-$i/mf
 perf stat -x, -e instructions,cycles,branch-misses,L1-icache-load-misses -o $OUT/$v-$i/perf.txt nice -n 5 /opt/alpha-compute/bin/b20$v lob touches --root $R --symbol SUIUSDT $FL --minute-flow $OUT/$v-$i/mf --out $OUT/$v-$i/t.csv > /dev/null 2>&1
 echo "$v $i $(grep -v '^#' $OUT/$v-$i/perf.txt | awk -F, '{printf "%s=%s ", $3, $1}')" >> $OUT/res.txt; done; done
cmp $OUT/head-1/t.csv $OUT/r1-1/t.csv && echo "CMP t.csv head==r1 off" >> $OUT/res.txt || echo "CMP DIFF" >> $OUT/res.txt
echo done >> $OUT/res.txt
IN
/data/benchrun.sh stand bash /data/tk064/perf10.sh > /data/tk064/chain7.log 2>&1
touch /data/tk064/chain7.done
