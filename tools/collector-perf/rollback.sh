#!/bin/bash
# Откат наката: rollback.sh <юнит> — вернуть прежний бинарь (`<путь>.prev-<md5>` из /data/perf/rollout-<юнит>.args) и прежние аргументы.
set -eu
UNIT=$1
F=/data/perf/rollout-$UNIT.args
[ -f "$F" ] || { echo "нет $F"; exit 2; }
BIN=$(grep -oP 'BIN=\K\S+' $F); PREV=$(grep -oP 'PREV=\K\S+' $F); ARGS=$(grep -oP 'ARGS="\K[^"]*' $F)
RESTART=$(grep -oP 'RESTART=\K\S+' $F); KILL=$(grep -oP 'KILL=\K\S+' $F); TSTOP=$(grep -oP 'TSTOP=\K\S+' $F); NOFILE=$(grep -oP 'NOFILE=\K\S+' $F)
echo "$(date -u +%FT%TZ) rollback $UNIT -> $PREV" >> /data/perf/rollout.log
systemctl stop $UNIT
cp $PREV $BIN.new && chmod +x $BIN.new && mv $BIN.new $BIN
systemd-run --unit=$UNIT --property=Restart=always --property=RestartSec=$RESTART --property=KillMode=$KILL --property=TimeoutStopSec=$TSTOP \
  --property=LimitNOFILE=$NOFILE --property=CPUAccounting=yes --property=MemoryAccounting=yes $BIN $ARGS
sleep 15
echo "state: $(systemctl is-active $UNIT)"
