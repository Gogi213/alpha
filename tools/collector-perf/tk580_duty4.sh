#!/bin/bash
# TK-580: effective clock of the decoder as a function of sleep length between 4 ms bursts (srv, natural load); interleaved, 3 passes
B=/data/tk580/alpha-gzbench7; F=/data/tk580/bx-raw.bin
for pass in 1 2 3; do
  for cfg in "0 0" "4000 6000" "4000 1000" "4000 200" "1000 9000"; do
    set -- $cfg
    echo -n "pass$pass work ${1}us sleep ${2}us: "
    perf stat -e task-clock,cycles $B collect gzbench --file $F --rounds 2 --decoders libdeflate --duty-us $1 --sleep-us $2 2>&1 | awk '/все:/{m=$4} /cycles/{g=$4} END{printf "mean %s ns, %s GHz\n", m, g}'
  done
done
mpstat 1 1 | tail -1
