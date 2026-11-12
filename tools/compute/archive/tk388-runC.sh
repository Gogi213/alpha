#!/bin/bash
# TK-388 / P-27 часть B, EVM-часть класса (SUE bsc, LAPTOP base; Q bsc — отдельной строкой): сетка 96, окно 01.09–14.10 (BBO Gate есть вне 01–07.10), mid=пул (med), тариф 3.25/1.3. 15 ядер.
cd /data/tk0388; PY=/data/tk0380/npvenv/bin/python; X=15
$PY tk388-bnb.py /data/tk0388/bbo/BNB.csv.gz || exit 1
export MODE=med DSTART=2026-09-01 ND=44 DAY0=1 FEE_TK=3.25 FEE_MK=1.3 BBOROOT=/data/tk0388/bbo SQDROOT=/srv/alpha-sda/tk387-dex SQYROOT=/srv/alpha-sda/tk387-dex/sync SEEDPER=1 FUNDCACHE=/data/tk0388/funding.json
mkdir -p oosC
for c in "bsc SUE" "base LAPTOP" "bsc Q"; do for T in 1.0 2.0 3.0 5.0; do echo "$c $T"; done; done | xargs -P $X -L1 bash -c 'ch=$0; c=$1; T=$2; mkdir -p oosC/$c; TSEL=$T /usr/bin/time -v -o oosC/$c-$T.time '$PY' -u tk388-converge.py $ch $c oosC/$c > oosC/$c-$T.log 2>&1'
touch oosC.done; date
