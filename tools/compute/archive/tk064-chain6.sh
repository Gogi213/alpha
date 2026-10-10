#!/bin/bash
# chain6 (TK-064 повтор Судьи 07.10): пара b19head/b19r1 одной сборкой. G2 perf stat touches SUI 20.09 (3 пары) -> pf8/res.txt; G2 grid d01 perf stat (3 пары) -> pf9/res.txt; G3 gate.sh SUI+AAVE -> gate4.out; G3 signals -> sig19/; затем G1 волна (chain91).
cd /data/tk064; rm -f chain6.done
S=/data/tk064/chain6.log; : > $S
R=/data/tk064/g2/root-SUIUSDT-2026-09-20; OUT=/data/tk064/pf8; rm -rf $OUT; mkdir -p $OUT; : > $OUT/res.txt
FL="--h3-mode notional --h3-usd 10000 --approach-bps 20"
cat > /data/tk064/perf8.sh <<'IN'
R=/data/tk064/g2/root-SUIUSDT-2026-09-20; OUT=/data/tk064/pf8
FL="--h3-mode notional --h3-usd 10000 --approach-bps 20"
for i in 1 2 3; do for v in head r1; do mkdir -p $OUT/$v-$i/mf
 perf stat -x, -e instructions,cycles,branch-misses,L1-icache-load-misses -o $OUT/$v-$i/perf.txt nice -n 5 /opt/alpha-compute/bin/b19$v lob touches --root $R --symbol SUIUSDT $FL --minute-flow $OUT/$v-$i/mf --out $OUT/$v-$i/t.csv > /dev/null 2>&1
 echo "$v $i $(grep -v '^#' $OUT/$v-$i/perf.txt | awk -F, '{printf "%s=%s ", $3, $1}')" >> $OUT/res.txt; done; done; echo done >> $OUT/res.txt
IN
echo "G2 touches $(date +%T)" >> $S; /data/benchrun.sh stand bash /data/tk064/perf8.sh >> $S 2>&1
echo "G2 grid $(date +%T)" >> $S; /data/benchrun.sh stand bash /data/tk064/gridperf.sh b19head b19r1 /data/tk064/pf9 3 d01 >> $S 2>&1
echo "G3 gate $(date +%T)" >> $S; REPS=1 /data/benchrun.sh stand /data/tk064/gate.sh /opt/alpha-compute/bin/b19head /opt/alpha-compute/bin/b19r1 /data/tk064/g4 e-sep:SUIUSDT:2026-09-20 e-sep:AAVEUSDT:2026-09-20 > /data/tk064/gate4.out 2>&1
echo "G3 signals $(date +%T)" >> $S; /data/benchrun.sh stand bash /data/tk064/tk064-sig.sh b19head b19r1 /data/tk064/sig19 > /data/tk064/sig19.out 2>&1
echo "G1 wave $(date +%T)" >> $S; bash /data/tk048/chain91.sh >> $S 2>&1
echo "all done $(date +%T)" >> $S; touch /data/tk064/chain6.done
