#!/bin/bash
# perf record touches SUI 20.09 для b17head / b17r1 / b17r1b -> /data/tk064/pf3/<v>/top.txt
R=/data/tk064/g2/root-SUIUSDT-2026-09-20; OUT=/data/tk064/pf3; mkdir -p $OUT
FL="--h3-mode notional --h3-usd 10000 --approach-bps 20"
for v in head r1 r1b; do mkdir -p $OUT/$v/mf
 perf record -F 999 -o $OUT/$v/perf.data nice -n 5 /opt/alpha-compute/bin/b17$v lob touches --root $R --symbol SUIUSDT $FL --minute-flow $OUT/$v/mf --out $OUT/$v/t.csv >/dev/null 2>&1
 perf report -i $OUT/$v/perf.data --stdio --no-children 2>/dev/null | grep -v "^#" | grep -v "^$" | head -25 > $OUT/$v/top.txt
done; echo done > $OUT/done
