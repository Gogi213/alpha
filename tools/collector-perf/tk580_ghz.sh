#!/bin/bash
# effective GHz (PMU cycles per second of task-clock) of each collector's reactor thread; 12 s each, all in parallel
for u in collect-bingx collect-okx collect-gate collect-binance soak-bybit; do
  PID=$(systemctl show -p MainPID --value $u.service)
  TID=$(ls /proc/$PID/task | while read t; do [ "$(cat /proc/$PID/task/$t/comm)" = rx-reactor ] && echo $t; done | head -1)
  ( perf stat -e task-clock,cycles,instructions -t $TID -- sleep 12 2>&1 | awk -v u=$u '/task-clock/{t=$1} /cycles/{c=$1} /instructions/{i=$1} END{gsub(",","",t);gsub(",","",c);gsub(",","",i); printf "%s on-CPU %.1f%% eff %.2f GHz IPC %.2f\n", u, t/120, c/t/1e6, i/c}' ) &
done
wait
mpstat 1 1 | tail -1
