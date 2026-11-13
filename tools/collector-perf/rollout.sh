#!/bin/bash
# G-022 collector-perf: накат нового бинаря на боевой коллектор одной биржи (по одной).
# Запуск на srv: [PROPS="-p Nice=-10 -p CPUWeight=400"] rollout.sh <юнит> <новый бинарь> [доп. аргументы коллектора...]
#   юнит: collect-binance | soak-bybit | collect-okx | collect-bingx
# Шаги: (1) итоговые аргументы собираются так, что доп. аргументы ЗАМЕНЯЮТ одноимённые уже стоящие в юните (повторный накат не дублирует флаги); новый бинарь с ними
# проверяется разбором (`--help` в конце: clap отвергает дубли и неизвестные флаги до остановки боевого процесса); (2) прежний бинарь — копия `<путь>.prev-<md5>`, новый
# кладётся атомарно (mv); (3) юнит останавливается SIGTERM и пересоздаётся `systemd-run` с теми же свойствами; (4) 30 с проверки: active, NRestarts=0, health свежий,
# кадры свежие; иначе автоматический откат `rollback.sh` и код 1. Прежние аргументы и свойства — /data/perf/rollout-<юнит>.env.
set -eu
UNIT=$1; NEW=$2; shift 2
EXTRA="$*"
PROPS=${PROPS:-}
LOG=/data/perf/rollout.log
echo "$(date -u +%FT%TZ) rollout $UNIT $NEW extra=[$EXTRA] props=[$PROPS]" >> $LOG
EXEC=$(systemctl show -p ExecStart --value $UNIT | grep -oP 'argv\[\]=\K[^;]*')
BIN=${EXEC%% *}
ARGS=${EXEC#* }
RESTART=$(systemctl show -p RestartUSec --value $UNIT)
KILL=$(systemctl show -p KillMode --value $UNIT)
TSTOP=$(systemctl show -p TimeoutStopUSec --value $UNIT)
NOFILE=$(systemctl show -p LimitNOFILE --value $UNIT)
OLDMD5=$(md5sum $BIN | cut -c1-8)
PREV=$BIN.prev-$OLDMD5
HEALTH=$(echo "$ARGS" | grep -oP -- '--health \K\S+')
cat > /data/perf/rollout-$UNIT.env <<ENV
BIN='$BIN'
PREV='$PREV'
ARGS='$ARGS'
RESTART='$RESTART'
KILL='$KILL'
TSTOP='$TSTOP'
NOFILE='$NOFILE'
ENV
# (1) итоговые аргументы
NEWARGS=$(python3 - "$ARGS" "$EXTRA" <<'PY'
import shlex, sys
a, e = shlex.split(sys.argv[1]), shlex.split(sys.argv[2])
names = {t for t in e if t.startswith("--")}
out, i = [], 0
while i < len(a):
    if a[i] in names:
        i += 1
        if i < len(a) and not a[i].startswith("--"):
            i += 1
        continue
    out.append(a[i])
    i += 1
print(" ".join(out + e))
PY
)
echo "итоговые аргументы: $NEWARGS" | tee -a $LOG
cp $NEW $BIN.check && chmod +x $BIN.check
if ! timeout 20 $BIN.check $NEWARGS --help > /tmp/rollout-parse.txt 2>&1; then
  rm -f $BIN.check
  echo "ОТКАЗ: разбор аргументов новым бинарём не прошёл (боевой процесс не тронут):" | tee -a $LOG
  head -5 /tmp/rollout-parse.txt | tee -a $LOG
  exit 3
fi
rm -f $BIN.check
# (2) бинарь
[ -f "$PREV" ] || cp -p $BIN $PREV
cp $NEW $BIN.new && chmod +x $BIN.new && mv $BIN.new $BIN
echo "binary $BIN: prev $OLDMD5 -> $(md5sum $BIN | cut -c1-8)" | tee -a $LOG
# (3) остановка и запуск
systemctl stop $UNIT
T_STOP=$(date +%s)
echo "stopped $(date -u +%FT%T.%3NZ)" | tee -a $LOG
systemd-run --unit=$UNIT --property=Restart=always --property="RestartSec=$RESTART" --property=KillMode=$KILL --property="TimeoutStopSec=$TSTOP" \
  --property=LimitNOFILE=$NOFILE --property=CPUAccounting=yes --property=MemoryAccounting=yes $PROPS $BIN $NEWARGS
echo "started $(date -u +%FT%T.%3NZ)" | tee -a $LOG
# (4) проверка за 30 с
OK=0
for i in $(seq 1 30); do
  sleep 1
  [ "$(systemctl is-active $UNIT)" = active ] || continue
  [ "$(systemctl show -p NRestarts --value $UNIT)" = 0 ] || { echo "NRestarts>0" | tee -a $LOG; break; }
  if python3 - "$HEALTH" "$T_STOP" <<'PY'
import json, sys, time
h = json.load(open(sys.argv[1]))
now = time.time() * 1000
ok = h["ts_ms"] > float(sys.argv[2]) * 1000 and now - h["ts_ms"] < 15000 and now - h["last_frame_ms"] < 10000
sys.exit(0 if ok else 1)
PY
  then OK=1; break; fi
done
if [ $OK = 1 ]; then
  echo "OK: $UNIT active, NRestarts=0, health и кадры свежие ($(date -u +%T))" | tee -a $LOG
else
  echo "СБОЙ проверки — откат: rollback.sh $UNIT" | tee -a $LOG
  /data/perf/rollback.sh $UNIT || true
  exit 1
fi
