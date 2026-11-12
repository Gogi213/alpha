#!/bin/bash
# TK-401: слой Solana новых mint по swaps3: AGG=med (медиана свопов секунды, решение Судьи 25.10 21:45) и last (чувствительность, как TK-392).
set -e
O=/data/tk0393/tk401; NX=${NX:-15}; PY=/data/tk0380/npvenv/bin/python; cd /data/tk0380
for A in med last; do
  mkdir -p $O/curve-sol3-$A
  AGG=$A $PY /data/tk0393/tk401-mid-sol3.py $O
  xargs -r -P $NX -L1 sh -c '[ -s '$O'/curve-sol3-'$A'/$0-curve.csv ] || '$PY' tk392-event.py $0 '$O'/mid-sol3-'$A'/$0.csv.gz '$O'/curve-sol3-'$A'/$0 $1 > '$O'/curve-sol3-'$A'/$0.log 2>&1' < <(sed 's/ *$//' $O/list-sol3-$A.txt)
done
echo DONE > $O/runsol3.done
