#!/bin/bash
# G-022 independent-client A/B: final run after the measurement end (BingX client ends 19:05:52 UTC, Binance 19:01:34).
# Start as a niced unit:  systemd-run --unit=indep-ab-finalize --on-calendar='2026-11-16 19:07:00 UTC' -p Nice=19 -p IOSchedulingClass=idle \
#   -p IOSchedulingPriority=7 /data/indep-ab/finalize.sh
# Writes everything to /data/indep-ab/final/ ; raw data (run1, run1bn, host) is only read.
set -u
cd /data/indep-ab
OUT=/data/indep-ab/final
mkdir -p "$OUT"
log() { echo "$(date -u +%H:%M:%S) $*" >> "$OUT/finalize.log"; }
ep() { date -u -d "$1" +%s; }

F=$(ep '2026-11-16 16:20:00')        # window start (client CPU cap lifted 16:15, first 5 min throttled)
TB=$(ep '2026-11-16 19:05:30')       # BingX window end (client ends 19:05:52)
TN=$(ep '2026-11-16 19:01:00')       # Binance window end (client ends 19:01:34)
START=$(ep '2026-11-16 16:10:52')    # clients start
H14=$(ep '2026-11-16 14:00:00'); H15=$((H14+3600)); H16=$((H14+7200)); H17=$((H14+10800)); H18=$((H14+14400)); H19=$((H14+18000))

log "start; waiting for client units"
for i in $(seq 1 90); do
  systemctl is-active --quiet indep-ab-bingx || systemctl is-active --quiet indep-ab-binance || break
  sleep 10
done
log "client units: bingx=$(systemctl is-active indep-ab-bingx) binance=$(systemctl is-active indep-ab-binance)"
systemctl stop indep-ab-bingx indep-ab-binance 2>/dev/null
systemctl stop indep-ab-hostsample indep-ab-perfexec2
for i in $(seq 1 30); do systemctl is-active --quiet indep-ab-perfexec2 || break; sleep 2; done
log "measurement units stopped: $(systemctl is-active indep-ab-bingx indep-ab-binance indep-ab-hostsample indep-ab-perfexec2 | tr '\n' ' ')"
date -u > "$OUT/stopped-at.txt"

# ---- heavy jobs in the background (all niced via the unit)
(python3 analyze.py bingx --from $F --to $TB --json "$OUT/an-bingx-full.json" > "$OUT/an-bingx-full.out" 2> "$OUT/an-bingx-full.err"; log "analyze bingx rc=$?") &
(python3 analyze.py binance --from $F --to $TN --json "$OUT/an-binance-full.json" > "$OUT/an-binance-full.out" 2> "$OUT/an-binance-full.err"; log "analyze binance rc=$?") &
export FINE=1
prof() { # $1 = outdir, $2.. = epoch hours; prints one line per (exchange, kind)
  out=$1; shift; hours="$*"; mkdir -p "$out"
  for spec in "okx /data/collect/okx/data" "bybit /data/turbo-probe/soak-bybit/data" "gate /data/collect/gate/data" "bingx /data/collect/bingx/data" "binance /data/collect/binance/data"; do
    set -- $spec
    for kind in trade book; do echo "$1 $2 $kind $out $hours"; done
  done
}
(
  { prof "$OUT/prodprof-17-19" $H17 $H18 $H19; prof "$OUT/prodprof-14-16" $H14 $H15 $H16; } \
  | xargs -P2 -L1 bash -c 'ex=$0; root=$1; kind=$2; out=$3; shift 3; python3 /data/indep-ab/prod_hour_profile.py $root $kind "$@" > $out/$ex-$kind.txt 2> $out/$ex-$kind.err'
  log "prod profiles done"
) &

# ---- light jobs
perf script -i host/perf-exec.data -F time,comm,pid,event,trace > "$OUT/perf1.txt" 2> "$OUT/perf1.err"
perf script -i host/perf-exec2.data -F time,comm,pid,event,trace > "$OUT/perf2.txt" 2> "$OUT/perf2.err"
python3 perf_at_hour.py "$OUT/perf1.txt" $H17 > "$OUT/perf-at-17.txt" 2>&1
python3 perf_at_hour.py "$OUT/perf2.txt" $H18 > "$OUT/perf-at-18.txt" 2>&1
python3 perf_at_hour.py "$OUT/perf2.txt" $H19 > "$OUT/perf-at-19.txt" 2>&1
log "perf done"
for h in 17 18 19; do python3 hourwin.py 2026-11-16T$h > "$OUT/hourwin-$h.txt" 2>&1; python3 at00.py 2026-11-16T$h > "$OUT/at00-$h.txt" 2>&1; python3 bingx_shard_spike.py $(ep "2026-11-16 $h:00:00") > "$OUT/bingx-shard-spike-$h.txt" 2>&1; done
log "hourwin/at00/shard-spike done"
python3 conditions.py --from $F --to $TB > "$OUT/conditions.txt" 2> "$OUT/conditions.err"
python3 hourly_steal_vs_queue.py > "$OUT/hourly-steal-vs-queue.txt" 2>&1
python3 steal_vs_load.py > "$OUT/steal-vs-load.txt" 2>&1
python3 steal_by_minute.py > "$OUT/steal-by-minute.txt" 2>&1
python3 queue_spikes_by_minute.py 300 > "$OUT/queue-spikes-by-minute.txt" 2>&1
python3 prod_nodisturb.py /data/collect/bingx/data $START $TB > "$OUT/nodisturb-bingx.txt" 2>&1
python3 prod_nodisturb.py /data/collect/binance/data $START $TN > "$OUT/nodisturb-binance.txt" 2>&1
log "light jobs done"
wait
log "ALL DONE"
touch "$OUT/DONE"
