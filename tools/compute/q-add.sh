#!/usr/bin/env bash
# Очередь счёта Steam Deck (`alpha-gridq`, CEO 27.09): положить задание — один процесс (обычно `bounce-grid` одних
# суток одной клетки). Демон `gridq.sh` сам держит занятыми ~7 ядер, пока хватает памяти, и уступает ночи/гейтам.
#
#   q-add.sh [--tag <роль/серия>] [--prio 0-9] [--mem-gb X] [--home <дом>] [--log <файл>] [--wait] -- <команда…>
#
#   --tag    имя серии (буквы, цифры, `._-`), видно в q-status; по умолчанию `misc`
#   --prio   0 — первым, 9 — последним (по умолчанию 5); внутри приоритета — по времени постановки
#   --mem-gb оценка пика памяти; без неё — по `--root <каталог>` команды: крупнейший символ-сутки
#            (`.events` × 64 Б, без сайдкара — байты бинлога × 12) + 2 ГБ, не меньше замеренного пика тех же суток
#            (`queue/peaks.tsv`); без `--root` — 4 ГБ
#   --home   рабочий каталог команды (по умолчанию текущий)
#   --log    куда stdout+stderr команды (по умолчанию queue/logs/<id>.log)
#   --wait   ждать конца задания и выйти с его кодом возврата (для скриптов, раньше гонявших DAY_JOBS сами)
#
# Задание — файл queue/pending/<prio>-<время нс>-<tag>-<pid>.job (кладётся атомарно: .tmp + mv). Итог — в done/ или
# failed/ с JOB_RC, JOB_PEAK_MB, JOB_OOM. Состояние — `q-status.sh`, файл queue/STATUS.
set -euo pipefail
Q="${GRIDQ_DIR:-$HOME/alpha/queue}"
TAG=misc; PRIO=5; MEM_GB=""; HOME_DIR="$PWD"; LOGF=""; WAIT=""
while [ $# -gt 0 ]; do
  case "$1" in
    --tag) TAG="$2"; shift 2;;
    --prio) PRIO="$2"; shift 2;;
    --mem-gb) MEM_GB="$2"; shift 2;;
    --home) HOME_DIR="$2"; shift 2;;
    --log) LOGF="$2"; shift 2;;
    --wait) WAIT=1; shift;;
    --) shift; break;;
    *) echo "q-add: неизвестный ключ $1 (команда — после --)" >&2; exit 2;;
  esac
done
[ $# -ge 1 ] || { echo "q-add: нужна команда после --" >&2; exit 2; }
[[ $TAG =~ ^[A-Za-z0-9._-]+$ ]] || { echo "q-add: --tag $TAG — только буквы, цифры, ._-" >&2; exit 2; }
[[ $PRIO =~ ^[0-9]$ ]] || { echo "q-add: --prio $PRIO — 0…9" >&2; exit 2; }
HOME_DIR=$(cd "$HOME_DIR" && pwd)
mkdir -p "$Q"/{pending,running,done,failed,logs}

mem_mb() {  # оценка пика: крупнейший символ-сутки каталога --root, не меньше замеренного пика этих суток
  local root="" prev="" a
  for a in "$@"; do [ "$prev" = --root ] && root="$a"; prev="$a"; done
  [ -n "$root" ] || { echo 4096; return; }
  case "$root" in /*) ;; *) root="$HOME_DIR/$root";; esac
  local best=0 f t n b est known
  for f in "$root"/*.binlog; do
    [ -e "$f" ] || continue
    t=$(readlink -f "$f")
    if [ -f "$t.events" ]; then n=$(awk '{print $3}' "$t.events"); b=$(( n * 64 ))
    else b=$(( $(stat -c %s "$t") * 12 )); fi
    [ "$b" -gt "$best" ] && best=$b
  done
  est=$(( best / 1048576 + 2048 ))   # замер 27.09 (e-aug, 3 потока): пик ≈ 2 ГБ + 10 × крупнейший бинлог
  known=$(awk -F'\t' -v r="$(readlink -f "$root")" '$1 == r && $2 > m {m = $2} END {print m + 0}' "$Q/peaks.tsv" 2>/dev/null || echo 0)
  known=$(( known * 11 / 10 ))
  echo $(( known > est ? known : est ))
}

if [ -n "$MEM_GB" ]; then MEM=$(awk -v g="$MEM_GB" 'BEGIN {printf "%d", g * 1024}'); else MEM=$(mem_mb "$@"); fi
ID="$PRIO-$(date +%s%N)-$TAG-$$"
[ -n "$LOGF" ] || LOGF="$Q/logs/$ID.log"
case "$LOGF" in /*) ;; *) LOGF="$HOME_DIR/$LOGF";; esac
TMP="$Q/pending/.tmp.$ID"
{
  printf 'JOB_ID=%q\nJOB_TAG=%q\nJOB_HOME=%q\nJOB_LOG=%q\nJOB_MEM_MB=%q\nJOB_ADDED=%q\nJOB_RETRY=0\n' \
    "$ID" "$TAG" "$HOME_DIR" "$LOGF" "$MEM" "$(date -u +%FT%TZ)"
  printf 'JOB_CMD=('; printf '%q ' "$@"; printf ')\n'
} > "$TMP"
mv "$TMP" "$Q/pending/$ID.job"
echo "$ID"
[ -n "$WAIT" ] || exit 0
while :; do
  for d in done failed; do
    if [ -f "$Q/$d/$ID.job" ]; then
      rc=$(awk -F= '$1 == "JOB_RC" {print $2}' "$Q/$d/$ID.job" | tail -1)
      exit "${rc:-1}"
    fi
  done
  sleep 5
done
