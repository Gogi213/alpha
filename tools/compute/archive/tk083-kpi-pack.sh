#!/bin/bash
# TK-083: все принятые круги после busy-replay в один csv (symbol,day,form,t0,exit,qty,entry,net,reason,fill)
cd /data/tk083/on || exit 2
echo "symbol,day_utc,form,t0_ns,exit_ns,qty,entry_vwap,entry_px,net_bps,reason,fill_frac,dir" > ../rounds-all.csv.tmp
for f in 2026-*/2026-*/t-bid-btc4h-q1/rounds.csv; do
  grep -v '^#' $f | awk -F, 'NR>1{print $1","$2","$3","$5","$12","$9","$14","$7","$10","$11","$13","$6}'
done >> ../rounds-all.csv.tmp
mv ../rounds-all.csv.tmp ../rounds-all.csv; touch ../pack.done
