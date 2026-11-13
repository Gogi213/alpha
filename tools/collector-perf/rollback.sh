#!/bin/bash
# Откат наката: rollback.sh <юнит> — вернуть прежний бинарь (`<путь>.prev-<md5>` из /data/perf/rollout-<юнит>.env) и прежние аргументы.
set -eu
UNIT=$1
F=/data/perf/rollout-$UNIT.env
[ -f "$F" ] || { echo "нет $F"; exit 2; }
. $F
echo "$(date -u +%FT%TZ) rollback $UNIT -> $PREV" >> /data/perf/rollout.log
systemctl stop $UNIT
cp $PREV $BIN.new && chmod +x $BIN.new && mv $BIN.new $BIN
systemd-run --unit=$UNIT --property=Restart=always --property="RestartSec=$RESTART" --property=KillMode=$KILL --property="TimeoutStopSec=$TSTOP" \
  --property=LimitNOFILE=$NOFILE --property=CPUAccounting=yes --property=MemoryAccounting=yes $BIN $ARGS
sleep 15
echo "state: $(systemctl is-active $UNIT)"
