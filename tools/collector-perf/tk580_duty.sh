#!/bin/bash
# TK-580: does a bursty (sleeping) duty cycle lower the effective clock? libdeflate decoder, continuous vs 4 ms work / 6 ms sleep
B=${B:-/data/tk580/alpha-gzbench7}; F=${F:-/data/tk580/bx-raw.bin}
for mode in "0 0" "4000 6000" "0 0" "4000 6000"; do
  set -- $mode
  echo "== duty ${1}us sleep ${2}us"
  perf stat -e task-clock,cycles,instructions $B collect gzbench --file $F --rounds 3 --decoders libdeflate --duty-us $1 --sleep-us $2 2>&1 | grep -E "все:|task-clock|cycles|instructions|elapsed"
done
mpstat 1 1 | tail -1
