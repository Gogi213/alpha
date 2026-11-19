#!/bin/bash
# TK-580 load test 2: A = old binary Nice 0, B = new binary Nice 0, C = new binary Nice -10; 5 shards x 1 lane each.
# Phases (80 s each): P0 quiet, P1 syscall storm (nice 19), P2 page-fault storm (nice 19).
L=$1; D=/data/tk580/ab-$L; mkdir -p $D
SH=book00,book01,trade00,mark00,mark01
start() { # name binary nice
  rm -rf $D/$1; mkdir -p $D/$1
  systemd-run --unit=tk580-ab-$L-$1 --collect --property=Nice=$3 /data/tk580/$2 collect run --exchange bingx --root $D/$1/data --health $D/$1/health.json \
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
start A alpha-old 0; sleep 15; start B alpha-new 0; sleep 15; start C alpha-new -10
sleep 70; snap t1
sleep 80; snap t2
for i in 1 2 3 4 5 6; do systemd-run --unit=tk580-sysl-$L-$i --collect --nice=19 bash -c 'timeout 80 sh -c "while :; do dd if=/dev/zero of=/dev/null bs=1 count=2000000 2>/dev/null; done"' >/dev/null; done
sleep 80; snap t3
for i in 1 2 3 4 5 6; do systemd-run --unit=tk580-pfl-$L-$i --collect --nice=19 python3 -c "
import mmap,time
end=time.time()+80
while time.time()<end:
    m=mmap.mmap(-1,256<<20)
    for o in range(0,256<<20,4096): m[o]=1
    m.close()
" >/dev/null; done
sleep 80; snap t4
sleep 10
echo done > $D/done
