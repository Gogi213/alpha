#!/usr/bin/env bash
# Ночной перенос закрытых суток записи с коллектора на счётную машину — Steam Deck (L8, 2026-09-22).
#
# Почему тянет приёмник, а не шлёт коллектор: дек стоит за домашним NAT (192.168.1.49),
# входящего адреса у него нет — прежний `tools/sync-to-compute.sh` (push на 13.140.29.171)
# для дека неприменим. Здесь обратная сторона той же копии: дек сам ходит к коллектору.
#
# Что копируется:
#   <SYMBOL>-<день>[-pN].binlog за вчерашние сутки UTC (сверка `alpha-verify.timer` в 00:20 UTC
#   уже прошла), instruments.csv, verify-<SYM>.status (маркеры K1), gaps.csv, clock.csv,
#   session.json и сводка сверки /opt/alpha/verify/<день>.log → root/verify-logs/;
#   поток `.200` тех же суток из `root/deep/` → ~/alpha/deep/ (G0, 23.09: единственная наша запись
#   200 уровней, нужна двойнику G1; сетка его не читает). Удаление на коллекторе — не здесь: ключ
#   только на чтение.
#
# Запуск: пользовательский таймер `alpha-pull.timer` в 01:15 UTC (см. tools/alpha-pull.*),
# вручную: ~/alpha/bin/sync-from-collector.sh [YYYY-MM-DD]. Идемпотентно (rsync -a --partial).
# Ключ дека `~/.ssh/id_ed25519` авторизован на коллекторе как
#   restrict,command="/usr/bin/rrsync -ro /opt/alpha" — только чтение, пути относительно /opt/alpha.
set -euo pipefail

DAY="${1:-$(date -u -d 'yesterday' +%F)}"
SRC_HOST="${ALPHA_COLLECTOR:-ubuntu@139.99.91.22}"
DST="${ALPHA_ROOT:-$HOME/alpha/root}"
DEEP="${ALPHA_DEEP:-$HOME/alpha/deep}"
KEY="${ALPHA_PULL_KEY:-$HOME/.ssh/id_ed25519}"
KH="$HOME/.ssh/known_hosts"
LOGDIR="$HOME/alpha/sync"
LOG="$LOGDIR/${DAY}.log"

mkdir -p "$DST/verify-logs" "$LOGDIR"
SSH="ssh -i $KEY -o BatchMode=yes -o UserKnownHostsFile=$KH -o StrictHostKeyChecking=yes -o ConnectTimeout=30"
{
  echo "== $(date -u +%FT%TZ) pull day=$DAY from $SRC_HOST -> $DST"
  nice -n 19 ionice -c3 rsync -a --partial --stats -e "$SSH" \
    --include="*-${DAY}*.binlog" \
    --include='instruments.csv' --include='verify-*.status' \
    --include='gaps.csv' --include='clock.csv' --include='session.json' \
    --exclude='*' \
    "$SRC_HOST:root/" "$DST/"
  mkdir -p "$DEEP"
  if ! nice -n 19 ionice -c3 rsync -a --partial -e "$SSH" --include="*-${DAY}*.binlog" --exclude='*' \
      "$SRC_HOST:root/deep/" "$DEEP/"; then
    echo "!! поток .200 за $DAY не перенесён"
  fi
  if ! nice -n 19 rsync -a -e "$SSH" "$SRC_HOST:verify/${DAY}.log" "$DST/verify-logs/"; then
    echo "!! сводки сверки за $DAY на коллекторе нет"
  fi
  echo "== $(date -u +%FT%TZ) done files=$(ls "$DST" | grep -c -- "-${DAY}" || true) deep=$(ls "$DEEP" | grep -c -- "-${DAY}" || true)"
} >> "$LOG" 2>&1
