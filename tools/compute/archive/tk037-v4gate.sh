#!/usr/bin/env bash
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
OUT=/data/tk037/qty-gate; G=/data/tk037/gate-roots; NEW=/opt/alpha-compute/bin/alpha-tk037-qty
one() { # tag sym day vroot
  mkdir -p $G/$1-1d; ln -sf $(readlink -f $4/$2-$3.binlog) $G/$1-1d/; cp $G/$1/session.json $G/$1-1d/; cp $G/$1/instruments.csv $G/$1-1d/; echo ok > $G/$1-1d/verify-$2.status
  nice -n 10 $NEW lob bounce-grid --root $G/$1-1d --symbol $2 --day $3 $RTT --order-usd 1000 --allow-unverified --queue-model prob:3 --threads 2 --h3-mode notional --h3-usd 10000 --stop-form pct1 --take-form 1to1 --out-dir $OUT/g3-$1-$3 > $OUT/g3-$1-$3.log 2>&1; echo "$1 $3 rc=$?" >> $OUT/v4gate3.rc; }
one MET METUSDT 2026-01-27 /data/tk037/vroots/e-2026-01 &
one EIGEN EIGENUSDT 2026-02-09 /data/tk037/vroots/e-2026-02 &
one ESPORTS ESPORTSUSDT 2026-07-18 /data/tk037/vroots/e-2026-07 &
wait; touch $OUT/ALL_DONE_V4GATE3
