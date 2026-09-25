#!/usr/bin/env bash
# Удаление на коллекторе закрытых суток, которые уже лежат на Steam Deck (владелец 2026-09-25:
# «на коллекторе лишнее — удалять»). Коллектор сам старые сутки не удаляет (~7 ГБ/сутки вместе с
# `.200`), а копия на Steam Deck после ночного забора — полная.
#
# Вызов — только с Steam Deck, отдельным ключом с forced command (ключ забора — только на чтение):
#   restrict,command="sudo -n /opt/alpha/tools/prune-pulled.sh" ssh-ed25519 … deck-prune
# Вход — stdin, строки «R|D <имя> <размер>» (R — root/, D — root/deep/): файлы, как они лежат на
# Steam Deck после забора (`tools/sync-from-collector.sh` строит список сам). Файл удаляется, только если:
#   - имя — <SYMBOL>-<день>[-pN].binlog, день раньше сегодняшнего UTC (идущие сутки не трогаем);
#   - сверка этих суток прошла: есть /opt/alpha/verify/<день>.log (иначе без файлов маркеры не получить);
#   - размер на коллекторе равен размеру на Steam Deck (недокачанный --partial короче — остаётся).
# Жёсткие ссылки сверки verify/<день>/<имя> удаляются вместе с файлом, иначе место не вернётся
# (грабля 21.09, docs/COMMANDS.md). Сводки verify/<день>.log и маркеры verify-*.status остаются.
set -euo pipefail

BASE="${ALPHA_BASE:-/opt/alpha}"   # подмена — только для проверки на копии; sudo окружение не передаёт
TODAY="${ALPHA_TODAY:-$(date -u +%F)}"
LOGDIR=$BASE/sync
mkdir -p "$LOGDIR"
LOG="$LOGDIR/prune-$TODAY.log"

del=0; kept=0; bad=0; bytes=0
while read -r kind name size; do
  if [[ ! $name =~ ^[A-Z0-9]+-([0-9]{4}-[0-9]{2}-[0-9]{2})(-p[0-9]+)?\.binlog$ ]]; then
    bad=$((bad + 1)); continue
  fi
  day=${BASH_REMATCH[1]}
  if [[ ! $size =~ ^[0-9]+$ ]]; then
    bad=$((bad + 1)); continue
  fi
  if [[ ! $day < $TODAY ]] || [[ ! -f $BASE/verify/$day.log ]]; then
    kept=$((kept + 1)); continue
  fi
  case $kind in
    R) paths=("$BASE/root/$name" "$BASE/verify/$day/$name") ;;
    D) paths=("$BASE/root/deep/$name") ;;
    *) bad=$((bad + 1)); continue ;;
  esac
  for f in "${paths[@]}"; do
    [[ -f $f ]] || continue
    if [[ $(stat -c %s "$f") == "$size" ]]; then
      rm -f -- "$f"; del=$((del + 1))
      [[ $f == "$BASE/verify/"* ]] || bytes=$((bytes + size))
    else
      kept=$((kept + 1))
    fi
  done
done

msg="$(date -u +%FT%TZ) prune deleted=$del kept=$kept bad=$bad bytes=$bytes"
echo "$msg" >> "$LOG"
echo "$msg"
