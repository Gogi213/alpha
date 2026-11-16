#!/bin/bash
# TK-580 A/B: two BingX collector copies (old = 29d981c0 prod binary, new = tiny gunzip) side by side, same flags/shards, same host, same time.
# usage: abrun.sh <label> <first: old|new> [seconds=300]
L=$1; FIRST=$2; SECS=${3:-300}; D=/data/tk580/ab-$L; mkdir -p $D
SECOND=new; [ "$FIRST" = new ] && SECOND=old
SH=book00,book01,book02,trade00,trade01,mark00,mark01
start() {
  n=$1; rm -rf $D/$n; mkdir -p $D/$n
  systemd-run --unit=tk580-ab-$L-$n --collect --property=Nice=0 /data/tk580/alpha-$n collect run --exchange bingx --root $D/$n/data --health $D/$n/health.json \
    --lanes 1 --reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --sub-in-reactor --only-shards $SH --duration-s $SECS >/dev/null
}
mpstat 1 $((SECS+45)) > $D/mpstat.txt &
start $FIRST; sleep 20; start $SECOND
sleep 70   # warm-up: subscriptions, path checks
for n in old new; do
  PID=$(systemctl show -p MainPID --value tk580-ab-$L-$n.service)
  TID=$(ls /proc/$PID/task | while read t; do [ "$(cat /proc/$PID/task/$t/comm)" = rx-reactor ] && echo $t; done | head -1)
  echo $PID $TID > $D/$n.ids
  cat /proc/$TID/schedstat > $D/$n.ss0; cp $D/$n/health.json $D/$n.h0.json
  (perf stat -e task-clock,cycles,instructions -t $TID -- sleep $((SECS-120)) > $D/$n.perfstat.txt 2>&1; cat /proc/$TID/schedstat > $D/$n.ss1; cp $D/$n/health.json $D/$n.h1.json) &
done
wait
sleep 5
echo done > $D/done
