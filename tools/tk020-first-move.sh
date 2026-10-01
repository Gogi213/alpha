#!/usr/bin/env bash
# TK-020: первое боевое включение режима переноса (Судья принял sb-move 6507239 при условии: первый прогон по суткам 01.10 — вручную
# от root, старый alpha-sb-push.timer выключить тем же шагом, которым включается alpha-sb-move.timer). Запускается разово юнитом
# systemd-run после закрытия суток 01.10 UTC (00:06 UTC 02.10 = 04:06 GMT+4). Итог — ~/tk020/first-move.status.
set -uo pipefail
export SB_KEYDIR=/home/ubuntu/.ssh
D=2026-10-01; ST=/home/ubuntu/tk020/first-move.status
say() { echo "$(date -u +%FT%TZ) $*" | tee -a "$ST"; }
systemctl disable --now alpha-sb-push.timer && say "старый alpha-sb-push.timer выключен"
nice -n 19 ionice -c3 bash /opt/alpha/tools/sb-move.sh "$D"; say "sb-move.sh $D: код $?"
grep -- "$D" /opt/alpha/sync/sb/move.log | tail -n 12 >> "$ST"
ls /opt/alpha/sync/sb | grep ^ALERT >> "$ST" || say "ALERT-файлов нет"
systemctl enable --now alpha-sb-move.timer && say "alpha-sb-move.timer включён (раз в час, :10 UTC)"
