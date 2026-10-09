#!/bin/bash
# imp.sh SYM DAY OBZIP TRGZ OUTDIR [flag]
sym=$1; d=$2; ob=$3; tr=$4; out=$5; fl=$6
BIN=/opt/alpha-compute/bin/alpha-tk035-steps
mkdir -p "$out"
unzip -p "$ob" | nice -n 10 $BIN lob import-archive --symbol $sym --day $d --ob - --trades <(gunzip -c "$tr") --instruments /data/alpha/root/instruments.csv --root "$out" $fl 2>&1 | tail -2
