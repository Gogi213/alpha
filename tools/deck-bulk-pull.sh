#!/usr/bin/env bash
# Разовая заливка счёта на Steam Deck со старой счётной машины (L8, 2026-09-22):
# записи суток, кэши касаний и подходов, дампы сеток. Тянет дек (он за домашним NAT),
# ключ авторизован на 13.140.29.171 как restrict,command="/usr/bin/rrsync -ro /opt/alpha-compute".
#
# Почему потоками по суткам: один TCP-поток через океан даёт ~1.5–2 МБ/с при домашнем
# канале 8 МБ/с — узкое место не ширина, а окно одного потока. Сутки не пересекаются,
# поэтому потоки не спорят за файлы. Идемпотентно: повтор докачивает недостающее.
#   systemd-run --user --unit=alpha-bulk-pull --collect ~/alpha/bin/deck-bulk-pull.sh
set -uo pipefail
SRC="${ALPHA_OLD_COMPUTE:-root@13.140.29.171}"
DST="${ALPHA_HOME:-$HOME/alpha}"
KEY="${ALPHA_PULL_KEY:-$HOME/.ssh/id_ed25519}"
DAYS="${BULK_DAYS:-2026-09-16 2026-09-17 2026-09-18 2026-09-19 2026-09-20 2026-09-21}"
LOG="$DST/sync/bulk-$(date -u +%Y%m%dT%H%M%SZ).log"
mkdir -p "$DST"/{root/verify-logs,study,b5,sync}
SSH="ssh -i $KEY -o BatchMode=yes -o StrictHostKeyChecking=yes -o ConnectTimeout=30"
{
  echo "== $(date -u +%FT%TZ) bulk pull from $SRC -> $DST; сутки: $DAYS"
  for day in $DAYS; do
    rsync -a --partial -e "$SSH" --include="*-${day}*.binlog" --exclude='*' \
      "$SRC:root/" "$DST/root/" &
  done
  # мелочь корня (пул, маркеры K1, сводки сверки) и кэши — своими потоками
  rsync -a --partial -e "$SSH" --include='instruments.csv' --include='verify-*.status' \
    --include='gaps.csv' --include='clock.csv' --include='session.json' \
    --include='verify-logs/' --include='verify-logs/**' --exclude='*' \
    "$SRC:root/" "$DST/root/" &
  rsync -a --partial -e "$SSH" "$SRC:study/" "$DST/study/" &
  rsync -a --partial -e "$SSH" "$SRC:b5/" "$DST/b5/" &
  wait
  echo "== $(date -u +%FT%TZ) done; du:"; du -sh "$DST"/{root,study,b5}
  for day in $DAYS; do
    echo "   $day: $(ls "$DST/root" | grep -c -- "-$day" || true) файлов"
  done
} >> "$LOG" 2>&1
