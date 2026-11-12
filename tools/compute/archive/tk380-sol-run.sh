#!/bin/bash
# TK-380: Solana — tx Helius -> дельты -> свопы (swaps2) -> USD -> отрывы v3 (lagtr2). Выход /data/tk0380/solout, DONE.json.
set -e
cd /data/tk0378; O=/data/tk0380/solout; mkdir -p $O
python3 - <<'PY'
import csv
r=csv.DictReader(open('/data/tk0378/tk380-pools-2026-10-23.csv')); seen={}
for x in r:
    if x['chain']=='solana': seen[x['token']]=x['coin']
w=csv.writer(open('/data/tk0380/solout/mints.csv','w')); w.writerow(['src','coin','mint'])
for m,c in seen.items(): w.writerow(['gate',c,m])
PY
python3 /data/tk0380/tk380-sol-conv.py /data/tk0380/sol $O/dl.csv
python3 tk378-sol-swaps2.py $O/mints.csv $O/swaps.csv $O/cov.csv $O/dl.csv
python3 tk378-sqd-usd.py $O/swaps.csv solusdt-1m.csv $O/swaps-usd.csv $O/usd-summary.csv
python3 tk378_sol_lag_trades2.py $O/lagtr $O/swaps-usd.csv
echo '{"rc":"ok"}' > $O/DONE.json
