#!/usr/bin/env bash
# Сторож на сервере: ждёт конца долгой работы и печатает одну итоговую строку + хвост журнала (владелец 17.11:
# «давай попробуем сторожа на сервере» — вместо опроса srv из треда, который съедает контекст).
#   tools/srv-wait.sh unit:<имя юнита>  [минут, по умолчанию 240]   — ждёт, пока юнит перестанет быть active/activating
#   tools/srv-wait.sh file:<путь>       [минут]                     — ждёт появления файла (например .../DONE)
# Запускать из треда фоном (Bash run_in_background): цикл крутится на сервере, тред получает одно уведомление по
# завершении. CHECK_HOST=srv (по умолчанию) | vps. Обрыв ssh убивает и серверный цикл — ничего не остаётся висеть.
set -euo pipefail
WHAT="${1:?unit:<имя> | file:<путь>}"
MIN="${2:-240}"
KEY=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o BatchMode=yes
     -o ConnectTimeout=15 -o ServerAliveInterval=30 -o ServerAliveCountMax=4)
case "${CHECK_HOST:-srv}" in srv) HOST=root@5.231.25.224 ;; vps) HOST=root@13.140.29.171 ;; *) echo "CHECK_HOST: srv|vps"; exit 2 ;; esac
case "$WHAT" in
  unit:*) U="${WHAT#unit:}"
    REMOTE="timeout ${MIN}m bash -c 'while systemctl is-active -q $U || [ \"\$(systemctl is-active $U)\" = activating ]; do sleep 20; done'; rc=\$?
      [ \$rc = 124 ] && echo \"WAIT-TIMEOUT $U ${MIN}m\" || systemctl show $U -p Result -p ExecMainStatus -p ActiveState --value | paste -sd' ' | sed \"s/^/DONE $U /\"
      journalctl -u $U -n 5 --no-pager -o cat 2>/dev/null | cut -c1-300" ;;
  file:*) F="${WHAT#file:}"
    REMOTE="timeout ${MIN}m bash -c 'until [ -e \"$F\" ]; do sleep 20; done' && echo \"DONE file $F\" || echo \"WAIT-TIMEOUT file $F ${MIN}m\"" ;;
  *) echo "unit:<имя> | file:<путь>"; exit 2 ;;
esac
ssh "${KEY[@]}" "$HOST" "$REMOTE"
