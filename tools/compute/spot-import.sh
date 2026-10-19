#!/usr/bin/env bash
# TK-312 (В-45): спот Bybit из TK-305 (sda) → суточные бинлоги v3/v4, корни по месяцам, как у фьючерса (e-YYYY-MM).
#   spot-import.sh import <список "SYM DAY"…>   импорт монето-суток (lob import-archive --spot --steps-from-day)
#   spot-import.sh verify <месяц YYYY-MM…>      lob verify по каждой монете месяца → verify-<SYM>.status (K1)
#   spot-import.sh verify-pairs <список "месяц SYM">   то же только по перечисленным парам (хвост после переимпорта)
# env: SRC (откуда, умолчание /srv/alpha-sda/tk305/spot), OUT (куда, /srv/alpha-sda/binlog-spot), BIN, JOBS (умолч. 15),
#      QTY_SHIFT (--qty-shift: размер >9,2e9 монет — PEPE/BONK/SHIB/MOG, умолч. нет),
#      TMP (временный стакан, умолч. /dev/shm/spot-import), LOG (tsv: итог каждой единицы).
# Разбор формата — docs/findings/spot-binlog-import-2026-10-19.md. Уже готовое пропускается.
set -uo pipefail
MODE="${1:?import|verify}"; shift
SRC="${SRC:-/srv/alpha-sda/tk305/spot}"; OUT="${OUT:-/srv/alpha-sda/binlog-spot}"
BIN="${BIN:?путь к PGO-бинарю alpha}"; JOBS="${JOBS:-15}"
TMP="${TMP:-/dev/shm/spot-import}"; LOG="${LOG:-$OUT/import.tsv}"
mkdir -p "$OUT" "$TMP"

one_import() { # $1=SYM $2=DAY ; строка в $LOG: sym day rc сек байт_бинлога сообщение_импорта
  local sym=$1 day=$2 root="$OUT/e-${2:0:7}" t0 rc=0 msg=""
  local ob="$SRC/ob/$sym/${day}_${sym}_ob200.data.zip" tr="$SRC/tr/$sym/${sym}_${day}.csv.gz"
  local tb="$TMP/$sym-$day.ob" tt="$TMP/$sym-$day.csv"
  mkdir -p "$root"
  [ -f "$root/$sym-$day.binlog" ] && { printf '%s\t%s\tskip\t0\t0\tуже есть\n' "$sym" "$day" >> "$LOG"; return; }
  [ -f "$ob" ] && [ -f "$tr" ] || { printf '%s\t%s\tmissing\t0\t0\tнет файла на бирже (TK-305)\n' "$sym" "$day" >> "$LOG"; return; }
  t0=$(date +%s)
  # steps-from-day читает стакан дважды — файлом (в /dev/shm), не потоком
  unzip -p "$ob" > "$tb" && gunzip -c "$tr" > "$tt" &&
    msg=$(nice -n 10 "$BIN" lob import-archive --spot --steps-from-day ${QTY_SHIFT:+--qty-shift $QTY_SHIFT} --symbol "$sym" --day "$day" \
            --ob "$tb" --trades "$tt" --root "$root" 2>&1)
  rc=$?
  [ $rc -ne 0 ] && rm -f "$root/$sym-$day.binlog" "$root/$sym-$day.binlog.part"
  printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$sym" "$day" "$rc" "$(( $(date +%s) - t0 ))" \
    "$(stat -c%s "$root/$sym-$day.binlog" 2>/dev/null || echo 0)" "$(echo "$msg" | tr '\n' ' ' | cut -c1-600)" >> "$LOG"
  rm -f "$tb" "$tt"
}

one_verify() { # $1=месяц $2=SYM
  local root="$OUT/e-$1"
  "$BIN" lob verify --symbol "$2" --root "$root" > "$root/verify-$2.log" 2>&1
  printf '%s\t%s\t%s\t%s\n' "$1" "$2" "$?" "$(cat "$root/verify-$2.status" 2>/dev/null || echo none)" >> "${LOG%.tsv}-verify.tsv"
}
export -f one_import one_verify
export SRC OUT BIN TMP LOG QTY_SHIFT

case "$MODE" in
  import)
    LIST="${1:?список "SYM DAY"}"
    xargs -a "$LIST" -r -P "$JOBS" -n 2 bash -c 'one_import "$@"' _ ;;
  verify)
    for m in "$@"; do
      ls "$OUT/e-$m" | grep -a '\.binlog$' | sed 's/-20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]\(-p[0-9]*\)\?\.binlog$//' | sort -u |
        while read -r s; do echo "$m $s"; done
    done | xargs -r -P "$JOBS" -n 2 bash -c 'one_verify "$@"' _ ;;
  verify-pairs)
    xargs -a "${1:?список \"месяц SYM\"}" -r -P "$JOBS" -n 2 bash -c 'one_verify "$@"' _ ;;
  *) echo "import|verify|verify-pairs"; exit 2 ;;
esac
rmdir "$TMP" 2>/dev/null || true
