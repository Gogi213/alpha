#!/usr/bin/env bash
# Один проход по бинлогам на символ-сутки (T-14, план «один проход» T-05, разбор Судьи
# docs/research/reviews/one-pass-plan-2026-09-26.md): `lob touches --levels-out` пишет касания,
# подходы, минутный ряд середины и уровни ОДНИМ реплеем; лента — рядом лёгким `lob trades`
# (в 23 раза дешевле касаний, docs/findings/steamdeck-throughput-2026-09-26.md). Всё сжимается
# на лету: сырой CSV символа живёт на диске не дольше одного символа-суток (В-106).
#
#   ALPHA_HOME=<эпоха> SYMS_FROM=<каталог со списком символов по суткам: <день>/touches-*.csv> \
#   SRC_PREFIX=<префикс суточных корней, напр. study/root-> OUT=<куда класть сутки> \
#   [BIN=bin/alpha-<хеш>] [JOBS=3] [REPEAT_MS=3600000] \
#   [TOUCH_FLAGS="--h3-mode notional --h3-usd 10000 --approach-bps 20 --approach-min-age-secs 900"] \
#   [KEEP="touches approaches mids1m levels trades"] [MIN_FREE_GB=8] \
#   tools/compute/export-pass.sh <сутки...>
#
# Выход: <OUT>/<день>/<вид>-<SYM>.csv.gz по видам из KEEP; <OUT>/<день>.done — сутки готовы;
# <OUT>/<день>.failed.txt — символы, упавшие в touches или trades (сутки всё равно готовы, как у
# скриптов П-02). Сутки пишутся в <OUT>/<день>.tmp и переименовываются целиком — оборванные
# сутки при возобновлении пересчитываются с нуля.
#
# Манифест (условие Судьи 4): <OUT>/manifest.txt — бинарник (имя и sha256), TOUCH_FLAGS,
# REPEAT_MS, KEEP. Первый запуск пишет его; следующий сверяет и ОТКАЗЫВАЕТ при расхождении —
# в одном каталоге не смешиваются сутки разных бинарников, окон или флагов.
#
# Время суток печатается в строке итога (wall, с) — это и замер для T-04 (JOBS 3 → 8).
set -euo pipefail
ALPHA_HOME="${ALPHA_HOME:?ALPHA_HOME обязателен}"
SYMS_FROM="${SYMS_FROM:?SYMS_FROM обязателен (каталог <день>/touches-*.csv — список символов суток)}"
SRC_PREFIX="${SRC_PREFIX:?SRC_PREFIX обязателен (например study/root-)}"
OUT="${OUT:?OUT обязателен}"
export BIN="${BIN:-bin/alpha}"
JOBS="${JOBS:-3}"
export REPEAT_MS="${REPEAT_MS:-3600000}"
export TOUCH_FLAGS="${TOUCH_FLAGS:---h3-mode notional --h3-usd 10000 --approach-bps 20 --approach-min-age-secs 900}"
export KEEP="${KEEP:-touches approaches mids1m levels trades}"
MIN_FREE_GB="${MIN_FREE_GB:-8}"

cd "$ALPHA_HOME"
[ -x "$BIN" ] || { echo "нет исполняемого $BIN" >&2; exit 1; }
for k in $KEEP; do
  case "$k" in touches|approaches|mids1m|levels|trades) ;;
    *) echo "KEEP: неизвестный вид $k (touches|approaches|mids1m|levels|trades)" >&2; exit 1 ;;
  esac
done
mkdir -p "$OUT"

# Манифест: реальный бинарник за симлинком — по имени цели и хешу содержимого.
bin_real=$(readlink -f "$BIN")
manifest=$(printf 'bin=%s\nsha256=%s\ntouch_flags=%s\nrepeat_window_ms=%s\nkeep=%s\n' \
  "$(basename "$bin_real")" "$(sha256sum "$bin_real" | cut -d' ' -f1)" "$TOUCH_FLAGS" "$REPEAT_MS" "$KEEP")
if [ -f "$OUT/manifest.txt" ]; then
  if ! diff <(echo "$manifest") "$OUT/manifest.txt" > /dev/null; then
    echo "отказ: манифест $OUT/manifest.txt не совпадает с этим запуском — другой бинарник/флаги/окно:" >&2
    diff <(echo "$manifest") "$OUT/manifest.txt" >&2 || true
    echo "новый каталог OUT для другого набора, старые сутки не трогаются" >&2
    exit 1
  fi
else
  echo "$manifest" > "$OUT/manifest.txt"
fi

for day in "$@"; do
  symsday="$SYMS_FROM/$day"
  export src="${SRC_PREFIX}${day}"
  if [ ! -d "$symsday" ] || [ ! -d "$src" ]; then
    echo "$(date -u +%FT%TZ) $day: нет $symsday или $src — сутки пропущены" >&2
    continue
  fi
  if [ -f "$OUT/$day.done" ]; then
    echo "$(date -u +%FT%TZ) $day: уже готово (метка .done) — пропуск"
    continue
  fi
  free_gb=$(df -BG --output=avail "$OUT" | tail -1 | tr -dc '0-9')
  if [ "$free_gb" -lt "$MIN_FREE_GB" ]; then
    echo "$(date -u +%FT%TZ) $day: свободно ${free_gb} ГБ < MIN_FREE_GB=$MIN_FREE_GB — стоп" >&2
    exit 1
  fi
  export dayout="$OUT/$day.tmp"
  rm -rf "$dayout"
  mkdir -p "$dayout"
  t0=$(date +%s)
  syms=$(ls "$symsday"/touches-*.csv* 2>/dev/null | sed -e 's#.*/touches-##' -e 's#\.csv.*$##' | sort -u)
  n_syms=$(echo "$syms" | grep -c . || true)
  echo "$syms" | xargs -r -P "$JOBS" -I{} bash -c '
    sym="$1"
    raw="$dayout/raw-$sym"
    mkdir -p "$raw"
    keep() { case " $KEEP " in *" $1 "*) return 0 ;; *) return 1 ;; esac; }
    ok=1
    # shellcheck disable=SC2086
    if nice -n 15 "$BIN" lob touches --root "$src" --symbol "$sym" $TOUCH_FLAGS \
        --repeat-window-ms "$REPEAT_MS" --out "$raw/touches-$sym.csv" \
        --levels-out "$raw/levels-$sym.csv" > "$raw/touches.log" 2>&1; then
      for k in touches approaches mids1m levels; do
        f="$raw/$k-$sym.csv"
        if keep "$k" && [ -f "$f" ]; then gzip -c "$f" > "$dayout/$k-$sym.csv.gz"; fi
        rm -f "$f"
      done
    else
      ok=0
    fi
    if [ "$ok" = 1 ] && keep trades; then
      if nice -n 15 "$BIN" lob trades --root "$src" --symbol "$sym" \
          --out "$raw/trades-$sym.csv" > "$raw/trades.log" 2>&1; then
        gzip -c "$raw/trades-$sym.csv" > "$dayout/trades-$sym.csv.gz"
      else
        ok=0
      fi
    fi
    if [ "$ok" = 1 ]; then
      rm -rf "$raw"
    else
      echo "$sym" >> "$dayout/failed.txt"
      tail -1 "$raw"/*.log >> "$dayout/failed.log" 2>/dev/null || true
      rm -f "$raw"/*.csv
    fi
  ' _ {}
  n_fail=0
  if [ -f "$dayout/failed.txt" ]; then
    n_fail=$(wc -l < "$dayout/failed.txt")
    mv "$dayout/failed.txt" "$OUT/$day.failed.txt"
    mv "$dayout/failed.log" "$OUT/$day.failed.log" 2>/dev/null || true
  fi
  rm -rf "$dayout"/raw-*
  rm -rf "${OUT:?}/$day"
  mv "$dayout" "$OUT/$day"
  touch "$OUT/$day.done"
  size=$(du -sm "$OUT/$day" | cut -f1)
  echo "$(date -u +%FT%TZ) $day: символов=$n_syms ошибок=$n_fail wall=$(( $(date +%s) - t0 ))с размер=${size}МБ JOBS=$JOBS"
done
echo "$(date -u +%FT%TZ) export-pass готово: $*"
