#!/bin/bash
# perf record по инструкциям (u) touches SUI 20.09: head и r1d -> pf6/<v>/top.txt (число семплов по символам)
R=/data/tk064/g2/root-SUIUSDT-2026-09-20; OUT=/data/tk064/pf6; mkdir -p $OUT
FL="--h3-mode notional --h3-usd 10000 --approach-bps 20"
for v in "$@"; do mkdir -p $OUT/$v/mf
 perf record -e instructions:u -c 1000003 -o $OUT/$v/perf.data nice -n 5 /opt/alpha-compute/bin/b17$v lob touches --root $R --symbol SUIUSDT $FL --minute-flow $OUT/$v/mf --out $OUT/$v/t.csv >/dev/null 2>&1
 perf report -i $OUT/$v/perf.data --stdio --no-children -n 2>/dev/null | grep -v "^#" | grep -v "^$" | head -16 > $OUT/$v/top.txt
done; echo done > $OUT/done
