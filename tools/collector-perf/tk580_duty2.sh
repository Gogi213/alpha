#!/bin/bash
# TK-580: bursty decoder (4 ms work / 6 ms sleep, like the reactor) vs host load and CPU reservation. libdeflate decoder; hogs at nice 19.
B=/data/tk580/alpha-gzbench7; F=/data/tk580/bx-raw.bin
run() { # label, bench prefix, hog cpu list ('' = no hogs)
  local label=$1 pre=$2 hogs=$3 pids=""
  if [ -n "$hogs" ]; then
    for c in $(echo $hogs | tr ',' ' '); do
      nice -n 19 taskset -c $c timeout 60 xz -9 -c /dev/urandom > /dev/null 2>&1 & pids="$pids $!"
    done
    sleep 2
  fi
  mpstat 1 12 > /tmp/duty2.mp &
  for i in 1 2; do
    echo -n "$label run$i: "
    $pre perf stat -e task-clock,cycles $B collect gzbench --file $F --rounds 2 --decoders libdeflate --duty-us 4000 --sleep-us 6000 2>&1 \
      | awk '/все:/{m=$4; b=$(NF-0)} /cycles/{g=$4} /task-clock/{t=$1} END{printf "mean %s ns, book p50 %s ns, %s GHz\n", m, b, g}'
  done
  wait %mpstat 2>/dev/null; awk '/Average/{print "   host idle", $12, "nice", $4, "sys", $5}' /tmp/duty2.mp | tail -1
  [ -n "$pids" ] && kill $pids 2>/dev/null; wait $pids 2>/dev/null; sleep 1
}
run "no hogs, unpinned        " "" ""
run "8 hogs (cpu0-7), unpinned" "" "0,1,2,3,4,5,6,7"
run "8 hogs (cpu0-7), bench->7" "taskset -c 7" "0,1,2,3,4,5,6,7"
run "7 hogs (cpu0-6), bench->7 reserved" "taskset -c 7" "0,1,2,3,4,5,6"
