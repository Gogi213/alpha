#!/bin/bash
# TK-580: decoder bench on srv under natural host load. usage: gzb.sh <label> [extra gzbench args]
L=$1; shift; D=/data/tk580
mpstat 1 70 > $D/gzb-$L.mpstat.txt &
MP=$!
$D/alpha-gzbench6 collect gzbench --file $D/bx-raw.bin --rounds 3 --decoders libdeflate,tiny "$@" > $D/gzb-$L.txt 2>&1
kill $MP 2>/dev/null; wait 2>/dev/null
awk '$1=="Average:" || /Average/ {print}' $D/gzb-$L.mpstat.txt | tail -1 >> $D/gzb-$L.txt
echo done >> $D/gzb-$L.txt
