#!/usr/bin/env bash
# Ночной перенос закрытых суток записи с коллектора на счётную машину
# (переезд счёта, владелец 2026-09-18: считать рядом с коллектором нельзя,
# счёт живёт на 13.140.29.171 рядом с watcher/massedit).
#
# Что копируется (только поток `.50` из корня, `deep/` не нужен сетке):
#   <SYMBOL>-<день>[-pN].binlog за вчерашние сутки UTC (сверка `alpha-verify.timer`
#   в 00:20 UTC уже прошла), instruments.csv, verify-<SYM>.status (маркеры K1 —
#   состояние на момент копии), gaps.csv, clock.csv, session.json, и сводка сверки
#   /opt/alpha/verify/<день>.log в verify-logs/.
# Запуск: alpha-sync.timer в 00:45 UTC (systemd, `tools/alpha-sync.service|timer`),
# вручную: sudo tools/sync-to-compute.sh [YYYY-MM-DD]. Идемпотентно (rsync -a --partial),
# nice/ionice/bwlimit — коллектор в приоритете. Ключ: /home/ubuntu/.ssh/id_compute_sync,
# на приёмнике ограничен `from="139.99.91.22",restrict`.
set -euo pipefail

DAY="${1:-$(date -u -d 'yesterday' +%F)}"
SRC=/opt/alpha/root
DST_HOST=root@13.140.29.171
DST=/opt/alpha-compute/root
KEY=/home/ubuntu/.ssh/id_compute_sync
KH=/home/ubuntu/.ssh/known_hosts
LOG=/opt/alpha/sync/${DAY}.log
mkdir -p /opt/alpha/sync

SSH="ssh -i $KEY -o BatchMode=yes -o UserKnownHostsFile=$KH -o StrictHostKeyChecking=yes -o ConnectTimeout=30"
{
  echo "== $(date -u +%FT%TZ) sync day=$DAY -> $DST_HOST:$DST"
  nice -n 19 ionice -c3 rsync -a --partial --stats --bwlimit=25000 -e "$SSH" \
    --include="*-${DAY}*.binlog" \
    --include='instruments.csv' --include='verify-*.status' \
    --include='gaps.csv' --include='clock.csv' --include='session.json' \
    --exclude='*' \
    "$SRC/" "$DST_HOST:$DST/"
  if [ -f "/opt/alpha/verify/${DAY}.log" ]; then
    nice -n 19 rsync -a -e "$SSH" "/opt/alpha/verify/${DAY}.log" "$DST_HOST:$DST/verify-logs/"
  fi
  echo "== $(date -u +%FT%TZ) done"
} >> "$LOG" 2>&1
