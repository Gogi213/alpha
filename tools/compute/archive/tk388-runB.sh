#!/bin/bash
# TK-388 / P-27 часть B (Solana, mid=exec: среднее последней покупки и продажи <=60 с, комиссия пула сокращается), окно 01-14.10, тариф владельца 3.25/1.3
cd /data/tk0388; PY=/data/tk0380/npvenv/bin/python; X=15
OBR=/srv/alpha-sda/tk387-gate/futures_usdt/orderbooks/202610; COINS="AGENCY ALLINU EMBER FONE HOOKED SI STAMP"
for c in $COINS; do for d in 01 02 03 04 05 06 07 08 09 10 11 12 13 14; do ls $OBR/${c}_USDT-202610$d*.csv.gz 2>/dev/null; done; done > bboB-files.txt
wc -l bboB-files.txt
xargs -a bboB-files.txt -n 12 -P $X $PY tk388-bbo-hour.py /data/tk0388/bbo
# свопы по монетам, с 30.09 (база 24 ч)
mkdir -p swB; awk -F, -v t0=1790726400 'NR>1 && $1>=t0 && $1<1792022400 {f="swB/" $5 ".csv"; if(!(f in h)){h[f]=1; print hdr > f}; print > f} NR==1{hdr=$0}' /srv/alpha-sda/tk387-sqdsol/swaps-usd.csv
touch bboB.done; date
export MODE=med DSTART=2026-10-01 ND=14 DAY0=1 FEE_TK=3.25 FEE_MK=1.3 BBOROOT=/data/tk0388/bbo SEEDPER=1 FUNDCACHE=/data/tk0388/funding.json
mkdir -p oosB
for c in $COINS; do for T in 1.0 2.0 3.0 5.0; do echo "$c $T"; done; done | xargs -P $X -L1 bash -c 'c=$0; T=$1; mkdir -p oosB/$c; SWFILE=swB/$c.csv TSEL=$T /usr/bin/time -v -o oosB/$c-$T.time '$PY' -u tk388-converge.py solana $c oosB/$c > oosB/$c-$T.log 2>&1'
touch oosB.done; date
