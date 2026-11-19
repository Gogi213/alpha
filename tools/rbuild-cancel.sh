#!/usr/bin/env bash
# TK-614: отмена запроса раннера /opt/rbuild (tools/srv-build-run.sh). Ставится на srv как /opt/rbuild/cancel.sh.
#   bash /opt/rbuild/cancel.sh <id>      (id раннер печатает первой строкой: «rbuild: id=… prio=…»)
# В очереди — снимает запись (раннер выходит с rc 73). Идёт сборка — останавливает только её scope `rbuild-<id>.scope`
# (systemctl stop своего юнита, не pkill); раннер видит метку cancel/<id>, результат не сохраняет, `install` не выполняется.
set -u
B="${RBUILD_DIR:-$(dirname "$(readlink -f "$0")")}"   # каталог, где лежит этот скрипт (на srv — /opt/rbuild)
ID=$(printf '%s' "${1:?id запроса}" | tr -c 'A-Za-z0-9_.' '_')
Q=$B/queue; RUN=$B/running; CAN=$B/cancel
f=$(LC_ALL=C ls "$Q" 2>/dev/null | grep -E -- "^[0-9]-[0-9]+-$(printf '%s' "$ID" | sed 's/\./\\./g')\$" | head -1)
if [ -n "$f" ]; then rm -f "$Q/$f"; echo "cancel: $ID снят с очереди"; exit 0; fi
if [ -e "$RUN/$ID" ]; then
  mkdir -p "$CAN"; touch "$CAN/$ID"
  pid=$(cat "$RUN/$ID")
  # scope создаётся через мгновение после взятия замка — повторяем stop, пока раннер не выйдет
  for i in $(seq 1 30); do
    [ -d "/proc/$pid" ] || { echo "cancel: $ID остановлен"; exit 0; }
    systemctl stop "rbuild-$ID.scope" 2>/dev/null
    sleep 1
  done
  echo "cancel: $ID — раннер $pid ещё жив через 30 с"; exit 1
fi
echo "cancel: $ID не найден (ни в очереди, ни в работе)"; exit 2
