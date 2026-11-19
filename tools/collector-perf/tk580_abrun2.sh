#!/bin/bash
# TK-580 load test: two copies of the NEW binary (7 shards, 1 lane each), A at Nice 0, B at Nice -10; phases: P0 quiet 100 s, P1 with our own nice-19 CPU load 100 s.
L=$1; D=/data/tk580/ab-$L; mkdir -p $D
SH=book00,book01,book02,trade00,trade01,mark00,mark01
start() {
  n=$1; ni=$2; rm -rf $D/$n; mkdir -p $D/$n
  systemd-run --unit=tk580-ab-$L-$n --collect --property=Nice=$ni /data/tk580/alpha-new collect run --exchange bingx --root $D/$n/data --health $D/$n/health.json \
    --lanes 1 --reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --sub-in-reactor --only-shards $SH --duration-s 420 >/dev/null
}
snap() { # $1 label
  for n in A B; do
    PID=$(systemctl show -p MainPID --value tk580-ab-$L-$n.service)
    TID=$(ls /proc/$PID/task | while read t; do [ "$(cat /proc/$PID/task/$t/comm)" = rx-reactor ] && echo $t; done | head -1)
    echo "$(date +%s.%N) $(cat /proc/$TID/schedstat) $(grep -E 'voluntary_ctxt|nonvoluntary' /proc/$TID/status | awk '{print $2}' | tr '\n' ' ')" > $D/$n.$1.ss
    cp $D/$n/health.json $D/$n.$1.h.json
  done
}
mpstat 1 330 > $D/mpstat.txt &
start A 0; sleep 20; start B -10
sleep 70; snap t1
sleep 100; snap t2
echo "load_start $(date +%s)" > $D/phase
for i in 1 2 3 4 5 6; do systemd-run --unit=tk580-loadx-$L-$i --collect --nice=19 bash -c 'timeout 100 sh -c "xz -9 -c /dev/urandom > /dev/null"' >/dev/null; done
sleep 100; snap t3
echo "load_end $(date +%s)" >> $D/phase
sleep 10
echo done > $D/done
