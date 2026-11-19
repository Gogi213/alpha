#!/bin/bash
# TK-580 v2 (srv): old vs new BingX copies side by side under the real hold_* load of window C (16.11 17:33) and under whatever else runs on the host.
# usage: tk580v2_run.sh <label> <first: old|new> "<phases>" [load nice, default 19]      run as a systemd unit, not from ssh
#   phases: W:<s> warm-up (not reported), Q:<s> quiet = nothing of mine, B:<s> 6 x memhog bw (streaming memcpy 2 x 256 MB each, nice 19), C:<s> 6 x memhog chase (random pointer chase over 512 MB each, nice 19), L:<s> load = hold_scan.py (5 procs) + hold_marks.py (2 procs) of /opt/arb-hold on /data/arb-hold/b2
#   env COPIES=0 — no copies (live reference only). Copies: 7 shards x 1 lane each = 14 sockets (live 30 -> 44 <= 48).
# Everything on the host is recorded 1 Hz by tk580v2_sampler.py (ambient load of other threads is part of the conditions, not controlled).
set -u
L=$1; FIRST=$2; PH=$3; NL=${4:-19}
V=/data/tk580/v2; D=$V/$L; mkdir -p $D
SH=book00,book01,book02,trade00,trade01,mark00,mark01
COPIES=${COPIES:-1}
TOTAL=$(echo "$PH" | tr ' ' '\n' | awk -F: '{s+=$2} END {print s}')
log() { echo "$(date -u +%T) $*" >> $D/driver.log; }
bin_of() { [ "$1" = OLD ] && echo /data/tk580/alpha-old || echo /data/tk580/alpha-new2; }
start() { # OLD|NEW
  rm -rf $D/$1; mkdir -p $D/$1
  systemd-run --unit=tk580v2-$L-$1 --collect --property=Nice=0 $(bin_of $1) collect run --exchange bingx --root $D/$1/data --health $D/$1/health.json \
    --lanes 1 --reactor --worker-inline --reactor-window-us 10000 --rx-stamp kernel --sub-in-reactor --only-shards $SH --duration-s $((TOTAL + 600)) >/dev/null
}
UN="tk580v2-$L-scan tk580v2-$L-marks"
[ "$COPIES" = 1 ] && UN="tk580v2-$L-OLD tk580v2-$L-NEW $UN"
log "start label=$L first=$FIRST phases=[$PH] load_nice=$NL total=${TOTAL}s copies=$COPIES"
{ date -u; uptime; free -g; ps -eo pid,ni,pcpu,rss,etime,args --sort=-rss | head -8 | cut -c1-200; } > $D/conditions.txt 2>&1
python3 $V/tk580v2_sampler.py $D/samp.jsonl $((TOTAL + 60)) $D $UN &
if [ "$COPIES" = 1 ]; then
  if [ "$FIRST" = old ]; then start OLD; sleep 15; start NEW; else start NEW; sleep 15; start OLD; fi
  sleep 3
  for n in OLD NEW; do   # counters of the reactor thread of each copy, one run for the whole session, 60 s intervals
    PID=$(systemctl show -p MainPID --value tk580v2-$L-$n.service)
    TID=$(ls /proc/$PID/task | while read t; do [ "$(cat /proc/$PID/task/$t/comm)" = rx-reactor ] && echo $t; done | head -1)
    echo "$(date +%s.%N)" > $D/perf.$n.t0
    perf stat -I 60000 -x, -e task-clock,cycles,instructions,cache-references,cache-misses,L1-dcache-load-misses,page-faults,context-switches,cpu-migrations \
      -t $TID -o $D/perf.$n.csv -- sleep $((TOTAL + 5)) &
  done
fi
: > $D/phases.tsv
snap() { [ "$COPIES" = 1 ] && for n in OLD NEW; do cp $D/$n/health.json $D/snap.$1.$n.json 2>/dev/null; done; }
mem_avail_gb() { awk '/MemAvailable/ {print int($2/1048576)}' /proc/meminfo; }
i=0
for P in $PH; do
  K=${P%%:*}; S=${P#*:}; i=$((i + 1)); ST=$(date +%s.%N); snap $i.a
  ABORT=0
  if [ "$K" = L ]; then
    if [ "$(mem_avail_gb)" -lt 26 ]; then log "phase $i L: SKIP load, MemAvailable $(mem_avail_gb) GB < 26"; K=LSKIP; else
      systemd-run --unit=tk580v2-$L-scan --collect --nice=$NL -p MemoryMax=24G -p OOMScoreAdjust=1000 bash -c \
        "cd /opt/arb-hold/tools/arb_stats && exec python3 hold_scan.py /data/arb-hold/b2 1794550440000 1794849900000 $D/scan 5 > $D/scan.log 2>&1" >/dev/null
      systemd-run --unit=tk580v2-$L-marks --collect --nice=$NL -p MemoryMax=12G -p OOMScoreAdjust=1000 bash -c \
        "cd /opt/arb-hold/tools/arb_stats && exec python3 hold_marks.py 1794550440000 1794849900000 $D/marks.pkl 2 > $D/marks.log 2>&1" >/dev/null
      log "phase $i L: load started nice=$NL, MemAvailable $(mem_avail_gb) GB"
    fi
  fi
  if [ "$K" = B ] || [ "$K" = C ]; then
    M=bw; [ "$K" = C ] && M=chase
    for j in 1 2 3 4 5 6; do systemd-run --unit=tk580v2-$L-hog$i-$j --collect --nice=19 /data/tk580/memhog $M $S >/dev/null; done
    log "phase $i $K: 6 x memhog $M ${S}s nice 19"
  fi
  END=$(awk -v t=$ST -v s=$S 'BEGIN {printf "%d", t + s}')
  while [ "$(date +%s)" -lt "$END" ]; do
    sleep 5
    if [ "$K" = L ] && [ "$ABORT" = 0 ]; then
      if [ "$(mem_avail_gb)" -lt 3 ]; then log "ABORT load: MemAvailable < 3 GB"; systemctl stop tk580v2-$L-scan.service tk580v2-$L-marks.service; ABORT=1
      elif ! systemctl is-active -q tk580v2-$L-scan.service && ! systemctl is-active -q tk580v2-$L-marks.service; then log "phase $i: both hold jobs ended on their own"; ABORT=2; fi
    fi
  done
  if [ "$K" = B ] || [ "$K" = C ]; then for j in 1 2 3 4 5 6; do systemctl stop tk580v2-$L-hog$i-$j.service 2>/dev/null; done; fi
  if [ "$K" = L ]; then
    systemctl stop tk580v2-$L-scan.service tk580v2-$L-marks.service 2>/dev/null
    log "phase $i L: load stopped; scan.log tail: $(tail -c 120 $D/scan.log | tr '\n' ' ')"
  fi
  snap $i.b
  printf "%s\t%s\t%s\t%s\t%s\n" $i $K $ST $(date +%s.%N) $ABORT >> $D/phases.tsv
  log "phase $i $K ${S}s done"
done
[ "$COPIES" = 1 ] && systemctl stop tk580v2-$L-OLD.service tk580v2-$L-NEW.service 2>/dev/null
wait
rm -rf $D/scan.part* $D/marks.pkl
echo done > $D/done
log "done"
