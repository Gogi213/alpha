#!/bin/bash
# G-022 collector-perf: накат нового бинаря на боевой коллектор одной биржи (по одной, после G6b).
# Запуск на srv: [PROPS="-p Nice=-10 -p CPUWeight=400"] rollout.sh <юнит> <новый бинарь> [доп. аргументы коллектора...]
#   юнит: collect-binance | soak-bybit | collect-okx | collect-bingx
# Шаги: прежний бинарь — копия `<путь>.prev-<md5>` рядом; новый кладётся атомарно на то же место; юнит останавливается SIGTERM (файлы закрываются штатно) и
# пересоздаётся `systemd-run` с теми же свойствами (Restart, KillMode, TimeoutStopSec, LimitNOFILE) + PROPS и прежними аргументами + доп. аргументами.
# Прежние аргументы и свойства — в /data/perf/rollout-<юнит>.args (для `rollback.sh`).
set -eu
UNIT=$1; NEW=$2; shift 2
EXTRA="$*"
PROPS=${PROPS:-}
LOG=/data/perf/rollout.log
echo "$(date -u +%FT%TZ) rollout $UNIT $NEW $EXTRA props=[$PROPS]" >> $LOG
EXEC=$(systemctl show -p ExecStart --value $UNIT | grep -oP 'argv\[\]=\K[^;]*')
BIN=$(echo "$EXEC" | awk '{print $1}')
ARGS=$(echo "$EXEC" | cut -d' ' -f2-)
RESTART=$(systemctl show -p RestartUSec --value $UNIT)
KILL=$(systemctl show -p KillMode --value $UNIT)
TSTOP=$(systemctl show -p TimeoutStopUSec --value $UNIT)
NOFILE=$(systemctl show -p LimitNOFILE --value $UNIT)
OLDMD5=$(md5sum $BIN | cut -c1-8)
echo "BIN=$BIN PREV=$BIN.prev-$OLDMD5 ARGS=\"$ARGS\" RESTART=$RESTART KILL=$KILL TSTOP=$TSTOP NOFILE=$NOFILE" > /data/perf/rollout-$UNIT.args
cp -p $BIN $BIN.prev-$OLDMD5 2>/dev/null || true
cp $NEW $BIN.new && chmod +x $BIN.new && mv $BIN.new $BIN
echo "binary $BIN: prev $OLDMD5 -> $(md5sum $BIN | cut -c1-8); args: $ARGS $EXTRA" | tee -a $LOG
systemctl stop $UNIT
systemd-run --unit=$UNIT --property=Restart=always --property=RestartSec=$RESTART --property=KillMode=$KILL --property=TimeoutStopSec=$TSTOP \
  --property=LimitNOFILE=$NOFILE --property=CPUAccounting=yes --property=MemoryAccounting=yes $PROPS $BIN $ARGS $EXTRA
sleep 20
echo "state: $(systemctl is-active $UNIT) pid $(systemctl show -p MainPID --value $UNIT)" | tee -a $LOG
