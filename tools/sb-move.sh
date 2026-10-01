#!/usr/bin/env bash
# Коллектор: закрытые сутки → Storage Box → сверка → удаление локальной копии (TK-020 шаг 4, владелец 02.10:
# «коллектор писал один день и сразу переносил его в сторедж бокс»; план принят Судьёй 02.10 с условиями 1–4,
# docs/research/reviews/tk020-collector-mode-2026-10-02.md). Запускается таймером alpha-sb-push раз в час (:10).
#   sb-move.sh            — все закрытые сутки, у которых остались локальные файлы, от старших к младшим (догон);
#   sb-move.sh <сутки>    — одни сутки (ручной случай).
# Заливку и сверку делает sb-push-day.sh (sha256 самого ящика). Локальные файлы суток удаляются, только если ВСЕ:
#   - сутки < сегодня UTC; ни один файл суток не открыт процессом (`fuser`); есть сверка суток verify/<сутки>.log
#     (verify-day читает эти файлы; ночью 00:20 UTC);
#   - по `root/` и `root/deep/` отдельно: у каждого файла манифеста сумма ящика = локальной, строк ящика = строк манифеста;
#   - файл не новее манифеста (не дописывался).
# Удаляется ровно список манифеста (не маска) + жёсткие ссылки verify/<сутки>/<имя>; файл, которого нет в манифесте
# (появился после), остаётся и уйдёт следующим прогоном. Сутки целиком либо ничего: любое расхождение → ничего не
# удалено, ALERT-<сутки>. Тревоги — $BASE/sync/sb/ALERT-*: <сутки> (сбой/зависло), disk (диск коллектора > 70 %),
# box (ящик > 85 %); их показывает tools/alpha-status.sh. Журнал — $BASE/sync/sb/move.log.
# Проверка на копии: ALPHA_TEST=1 ALPHA_BASE=<копия> SB_REMOTE_PREFIX=alpha/tk020-test ALPHA_TODAY=<сутки>.
set -uo pipefail
export LC_ALL=C
BASE="${ALPHA_BASE:-/opt/alpha}"; RP="${SB_REMOTE_PREFIX:-alpha}"; TODAY="${ALPHA_TODAY:-$(date -u +%F)}"
ROOT=$BASE/root; OUT=$BASE/sync/sb; mkdir -p "$OUT"; touch "$OUT/done.txt"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SBH=u677479@u677479.your-storagebox.de
SSHC="ssh -p 23 -i $HOME/.ssh/id_storagebox -o BatchMode=yes -o ServerAliveInterval=15 -o ServerAliveCountMax=4"
ML=$OUT/move.log
say() { echo "== $(date -u +%FT%TZ) $*" >> "$ML"; }
exec 9> "$OUT/move.lock"; flock -n 9 || { say "уже идёт другой прогон — выход"; exit 0; }
YESTERDAY=$(date -u -d "$TODAY - 1 day" +%F)

alert() { echo "$(date -u +%FT%TZ) $2" > "$OUT/ALERT-$1"; say "ALERT-$1: $2"; }
clear_alert() { rm -f "$OUT/ALERT-$1"; }

# --- тревоги по месту: диск коллектора и ящик ---
disk=${DISK_USE_FAKE:-$(df --output=pcent "$BASE" | tail -1 | tr -dc 0-9)}
if [ "$disk" -gt 70 ]; then alert disk "диск коллектора $disk % (> 70)"; else clear_alert disk; fi
box=${BOX_USE_FAKE:-$($SSHC $SBH df 2>/dev/null | awk '/\/home|Mounted/ {last=$5} END {gsub("%", "", last); print last}')}
if [[ ${box:-} =~ ^[0-9]+$ ]]; then
  if [ "$box" -gt 85 ]; then alert box "ящик заполнен на $box % (> 85)"; else clear_alert box; fi
else alert box "не удалось прочитать заполнение ящика (df: '${box:-}')"; fi

# --- сутки с локальными файлами ---
if [ $# -ge 1 ]; then DAYS=("$1")
else
  mapfile -t DAYS < <( (ls "$ROOT" "$ROOT/deep" 2>/dev/null) | grep -oE "^[A-Z0-9]+-[0-9]{4}-[0-9]{2}-[0-9]{2}(-p[0-9]+)?\.binlog$" \
    | grep -oE "[0-9]{4}-[0-9]{2}-[0-9]{2}" | sort -u)
fi
[ ${#DAYS[@]} -gt 0 ] || { say "локальных суток нет"; exit 0; }

move_day() {
  local D=$1 late=0
  [[ $D < $TODAY ]] || { say "$D не закрыт (сегодня $TODAY) — пропуск"; return 0; }
  # «поздно» — если сутки старше вчерашних или уже 03:00 UTC следующего дня: тогда задержка — тревога, а не норма
  if [[ $D < $YESTERDAY ]] || [ "$(date -u +%H)" -ge 3 ] || [ -n "${ALPHA_TEST:-}" ]; then late=1; fi
  # открытые файлы суток
  local f open=0
  while IFS= read -r f; do fuser -s "$f" 2>/dev/null && { open=$((open + 1)); say "$D: открыт $f"; }; done \
    < <( (cd "$ROOT" && ls | grep -E "^[A-Z0-9]+-$D(-p[0-9]+)?\.binlog$" | sed "s|^|$ROOT/|"; \
          [ -d "$ROOT/deep" ] && cd "$ROOT/deep" && ls | grep -E "^[A-Z0-9]+-$D(-p[0-9]+)?\.binlog$" | sed "s|^|$ROOT/deep/|") )
  if [ "$open" -gt 0 ]; then
    [ $late = 1 ] && alert "$D" "$open файлов суток открыты процессом — перенос отложен, повтор через час" || say "$D: открытых файлов $open — повтор через час"
    return 0
  fi
  # заливка + сверка (sb-push-day.sh: rsync, sha256 ящика, done.txt, манифест)
  bash "$HERE/sb-push-day.sh" "$D" || true
  local kind src MAN bad=0 total=0 nloc nrem
  for kind in root deep; do
    src=$ROOT; [ $kind = deep ] && src=$ROOT/deep
    MAN=$OUT/$kind-$D.sha256
    [ -f "$MAN" ] || { [ -d "$src" ] && ls "$src" | grep -qE "^[A-Z0-9]+-$D(-p[0-9]+)?\.binlog$" && { bad=$((bad + 1)); say "$D $kind: нет манифеста"; }; continue; }
    nloc=$(wc -l < "$MAN"); nrem=$(wc -l < "$MAN.remote" 2>/dev/null || echo 0); total=$((total + nloc))
    [ "$nloc" -eq 0 ] && continue
    [ "$nrem" -ge "$nloc" ] || { bad=$((bad + 1)); say "$D $kind: на ящике строк $nrem < манифеста $nloc"; }
    local h n r
    # файл суток, которого нет в манифесте: новее манифеста (появился после) — остаётся; старше (нечитаем) — сбой
    while read -r n; do
      [ "$src/$n" -nt "$MAN" ] && { say "$D $kind/$n: новее манифеста — останется на следующий прогон"; continue; }
      bad=$((bad + 1)); say "$D $kind/$n: есть локально, нет в манифесте (нечитаем?)"
    done < <(comm -23 <(ls "$src" | grep -E "^[A-Z0-9]+-$D(-p[0-9]+)?\.binlog$" | sort) <(awk '{print $2}' "$MAN" | sort))
    while read -r h n; do
      r=$(awk -v f="$n" '$2 == f {print $1}' "$MAN.remote" 2>/dev/null)
      [ "$r" = "$h" ] || { bad=$((bad + 1)); say "$D $kind/$n: сумма ящика '${r:-нет}' ≠ локальной"; }
    done < "$MAN"
  done
  if [ "$bad" -gt 0 ]; then alert "$D" "сверка не прошла ($bad расхождений) — локальные файлы оставлены"; return 0; fi
  if [ "$total" -eq 0 ]; then say "$D: файлов нет"; clear_alert "$D"; return 0; fi
  if [ ! -f "$BASE/verify/$D.log" ]; then
    [ $late = 1 ] && alert "$D" "нет сверки verify/$D.log — удаление отложено" || say "$D: ждёт сверки verify/$D.log"
    return 0
  fi
  # удаление: ровно манифест, не новее манифеста
  local del=0 kept=0 bytes=0 sz
  for kind in root deep; do
    src=$ROOT; [ $kind = deep ] && src=$ROOT/deep
    MAN=$OUT/$kind-$D.sha256; [ -f "$MAN" ] || continue
    while read -r h n; do
      [ -f "$src/$n" ] || continue
      if [ "$src/$n" -nt "$MAN" ]; then kept=$((kept + 1)); say "$D $kind/$n: новее манифеста — оставлен"; continue; fi
      sz=$(stat -c %s "$src/$n"); rm -f -- "$src/$n" "$BASE/verify/$D/$n"
      del=$((del + 1)); bytes=$((bytes + sz))
    done < "$MAN"
  done
  say "MOVED $D: удалено файлов $del, байт $bytes, оставлено $kept"
  clear_alert "$D"
}

for D in "${DAYS[@]}"; do move_day "$D"; done
