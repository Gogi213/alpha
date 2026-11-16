#!/bin/bash
# TK-580: bursty decoder (4 ms work / 6 ms sleep) vs host load and CPU reservation, on a host where nothing else runs (Sofia). libdeflate; hogs nice 19.
B=${B:-/opt/alpha-compute/tk580/alpha-gzbench7}; F=${F:-/opt/alpha-compute/tk580/bx-raw.bin}
N=$(nproc); LAST=$((N-1)); ALL=$(seq -s, 0 $LAST); REST=$(seq -s, 0 $((LAST-1)))
run() {
  local label=$1 pre=$2 hogs=$3 pids=""
  if [ -n "$hogs" ]; then for c in $(echo $hogs | tr ',' ' '); do nice -n 19 taskset -c $c timeout 90 xz -9 -c /dev/urandom > /dev/null 2>&1 & pids="$pids $!"; done; sleep 2; fi
  vmstat 1 14 > /tmp/duty3.vm &
  for i in 1 2 3; do
    echo -n "$label run$i: "
    $pre perf stat -e task-clock,cycles $B collect gzbench --file $F --rounds 2 --decoders libdeflate --duty-us 4000 --sleep-us 6000 2>&1 \
      | awk '/все:/{m=$4} /cycles/{g=$4} END{printf "mean %s ns, %s GHz\n", m, g}'
  done
  wait %vmstat 2>/dev/null; awk 'NR>2{i+=$15;n++} END{printf "   host idle %.1f%% (vmstat)\n", i/n}' /tmp/duty3.vm
  [ -n "$pids" ] && kill $pids 2>/dev/null; wait $pids 2>/dev/null; sleep 1
}
run "no hogs, unpinned" "" ""
run "$N hogs on cpu0-$LAST, bench unpinned" "" "$ALL"
run "$N hogs on cpu0-$LAST, bench pinned to $LAST" "taskset -c $LAST" "$ALL"
run "$((N-1)) hogs on cpu0-$((LAST-1)), bench pinned to $LAST (reserved)" "taskset -c $LAST" "$REST"
