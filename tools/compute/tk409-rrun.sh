#!/bin/bash
# TK-409 блок R (П-30 §11): резкие уходы DEX X=10/15/20 %, пул как у tk409-pool.sh (EVM tk392/mid + Solana tk407/mid-sol3-med), без Q-пробы отдельно (Q включён); ядер 15
B=/data/tk0393/tk409/R; PY=/data/tk0380/npvenv/bin/python; S=/data/tk0393; export GEV=/data/tk0393/tk401/gev2all PY S B
mkdir -p $B; cd $B; rm -f R.done
{ for f in /data/tk0380/tk392/mid/*.csv.gz; do echo "$(basename $f .csv.gz) $f evm"; done
  for f in /data/tk0393/tk407/mid-sol3-med/*.csv.gz; do echo "$(basename $f .csv.gz) $f sol"; done; } > coins.lst
one() { [ -f $B/$1.done ] && return; $PY $S/tk409-ev-r.py $1 $2 $B/R-$1 > $B/R-$1.log 2>&1; touch $B/$1.done; }
export -f one
xargs -P 15 -L1 bash -c 'one $0 $1' < coins.lst
touch $B/R.done
