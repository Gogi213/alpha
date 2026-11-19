#!/bin/bash
# TK-580 load test 3: A = old binary, B = new binary, C = new binary (replica of B: noise level); all Nice 0, 5 shards x 1 lane.
# Phases (80 s each): P0 quiet, P1 memory bandwidth hog (6 x memhog bw, nice 19), P2 memory latency hog (6 x memhog chase, nice 19).
L=$1; D=/data/tk580/ab-$L; mkdir -p $D
SH=book00,book01,trade00,mark00,mark01
start() { # name binary
  rm -rf $D/$1; mkdir -p $D/$1
  systemd-run --unit=tk580-ab-$L-$1 --collect --property=Nice=0 /data/tk580/$2 collect run --exchange bingx --root $D/$1/data --health $D/$1/health.json \
    --lanes 1 --reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --sub-in-reactor --only-shards $SH --duration-s 520 >/dev/null
}
snap() {
  for n in A B C; do
    PID=$(systemctl show -p MainPID --value tk580-ab-$L-$n.service)
    TID=$(ls /proc/$PID/task | while read t; do [ "$(cat /proc/$PID/task/$t/comm)" = rx-reactor ] && echo $t; done | head -1)
    echo "$(date +%s.%N) $(cat /proc/$TID/schedstat) $(grep -E 'voluntary_ctxt|nonvoluntary' /proc/$TID/status | awk '{print $2}' | tr '\n' ' ')" > $D/$n.$1.ss
    cp $D/$n/health.json $D/$n.$1.h.json
  done
}
mpstat 1 400 > $D/mpstat.txt &
start A alpha-old; sleep 15; start B alpha-new; sleep 15; start C alpha-new
sleep 70; snap t1
sleep 80; snap t2
[ -n "$NOHOG" ] || for i in 1 2 3 4 5 6; do systemd-run --unit=tk580-bwl-$L-$i --collect --nice=19 /data/tk580/memhog bw 80 >/dev/null; done
sleep 80; snap t3
[ -n "$NOHOG" ] || for i in 1 2 3 4 5 6; do systemd-run --unit=tk580-chl-$L-$i --collect --nice=19 /data/tk580/memhog chase 80 >/dev/null; done
sleep 80; snap t4
sleep 10
echo done > $D/done
