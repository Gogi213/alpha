#!/bin/bash
# TK-580: low-overhead perf on live BingX collector. usage: perf1.sh <label> <seconds>
L=$1; S=${2:-45}; D=/data/tk580; mkdir -p $D
PID=$(systemctl show -p MainPID --value collect-bingx.service)
cp /data/collect/bingx/health.json $D/$L.health0.json
mpstat 1 $((S+2)) > $D/$L.mpstat.txt &
pidstat -t -p $PID 1 $S > $D/$L.pidstat.txt &
perf record -F 1999 -p $PID -o $D/$L.perf.data -- sleep $S > $D/$L.perf.log 2>&1
wait
cp /data/collect/bingx/health.json $D/$L.health1.json
perf report -i $D/$L.perf.data --no-children --sort comm,dso,sym --stdio 2>/dev/null | head -150 > $D/$L.report.txt
perf report -i $D/$L.perf.data --no-children --comm rx-reactor --sort dso,sym --stdio 2>/dev/null | head -120 > $D/$L.report-reactor.txt
echo done > $D/$L.done
