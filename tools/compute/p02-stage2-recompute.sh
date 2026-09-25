#!/usr/bin/env bash
# П-02, вторая волна (Г-07/Г-88): пересчёт touches новым бинарником
# (`depth_behind_lots`, T2; `repeat_count` на заданном окне `--repeat-window-ms`)
# по пулу и суткам, со сжатием на лету — полный touches-<SYM>.csv (61+ колонок)
# на диск не пишется дольше одного символа-суток, остаётся только компактная
# строка на касание (ключ + ended_by_death + depth_behind_lots + repeat_count).
# Диск на Steam Deck ограничен (В-106) — полные CSV на 54 сутках его исчерпают.
#
# Пул суток/символов берётся из существующего кэша D20 (список файлов
# touches-*.csv), не пересобирается заново — так проверка «число касаний на
# сутки совпадает с прежним кэшем» (задание) делается по построению (тот же
# набор символов).
#
#   ALPHA_HOME=<эпоха, напр. epochs/e-aug> D20=<каталог кэша D20 эпохи> \
#   SRC_PREFIX=<префикс суточных корней, напр. study/root-> \
#   OUT=<куда класть сжатые сутки> [BIN=bin/alpha-3a9fe23] [REPEAT_MS=300000] [JOBS=3] \
#   tools/compute/p02-stage2-recompute.sh <сутки...>
#
# Возобновляемо: сутки с файлом-меткой <OUT>/<день>.done пропускаются повторно.
set -euo pipefail
ALPHA_HOME="${ALPHA_HOME:?ALPHA_HOME обязателен}"
D20="${D20:?D20 обязателен (каталог кэша с touches-*.csv для списка символов)}"
SRC_PREFIX="${SRC_PREFIX:?SRC_PREFIX обязателен (например study/root-)}"
OUT="${OUT:?OUT обязателен}"
export BIN="${BIN:-bin/alpha-3a9fe23}"
export REPEAT_MS="${REPEAT_MS:-300000}"
JOBS="${JOBS:-3}"

cd "$ALPHA_HOME"
mkdir -p "$OUT"
COMPACT_HEADER="symbol,day_utc,side,price_tick,touch_index,ended_by_death,depth_behind_lots,repeat_count"

for day in "$@"; do
  d20day="$D20/$day"
  export src="${SRC_PREFIX}${day}"
  if [ ! -d "$d20day" ] || [ ! -d "$src" ]; then
    echo "$(date -u +%FT%TZ) $day: нет $d20day или $src — сутки пропущены" >&2
    continue
  fi
  if [ -f "$OUT/$day.done" ]; then
    echo "$(date -u +%FT%TZ) $day: уже готово (метка .done) — пропуск"
    continue
  fi
  export tmpdir
  tmpdir=$(mktemp -d)
  cat > "$tmpdir/extract.awk" <<'AWK'
NR==1 { for (i=1;i<=NF;i++) h[$i]=i; next }
{ print sym","$h["day_utc"]","$h["side"]","$h["price_tick"]","$h["touch_index"]","$h["ended_by_death"]","$h["depth_behind_lots"]","$h["repeat_count"] }
AWK
  outfile="$OUT/$day.csv"
  echo "$COMPACT_HEADER" > "$outfile.tmp"
  syms=$(ls "$d20day"/touches-*.csv 2>/dev/null | sed -e 's#.*/touches-##' -e 's#\.csv$##')
  n_syms=$(echo "$syms" | grep -c . || true)
  echo "$syms" | xargs -r -P "$JOBS" -I{} bash -c '
    sym="$1"
    raw="$tmpdir/$sym.csv"
    if nice -n 15 "$BIN" lob touches --root "$src" --symbol "$sym" \
        --h3-mode notional --h3-usd 10000 --repeat-window-ms "$REPEAT_MS" \
        --out "$raw" > "$tmpdir/$sym.log" 2>&1; then
      awk -F, -v sym="$sym" -f "$tmpdir/extract.awk" "$raw" >> "$tmpdir/compact-$sym.csv"
      rm -f "$raw" "$tmpdir/$sym.log"
    else
      echo "$sym" >> "$tmpdir/failed.txt"
    fi
  ' _ {}
  cat "$tmpdir"/compact-*.csv >> "$outfile.tmp" 2>/dev/null || true
  n_fail=0
  if [ -f "$tmpdir/failed.txt" ]; then
    n_fail=$(wc -l < "$tmpdir/failed.txt")
    cp "$tmpdir/failed.txt" "$OUT/$day.failed.txt"
  fi
  mv "$outfile.tmp" "$outfile"
  gzip -f "$outfile"
  n_rows=$(zcat "$outfile.gz" | wc -l)
  rm -rf "$tmpdir"
  touch "$OUT/$day.done"
  echo "$(date -u +%FT%TZ) $day: символов=$n_syms строк=$n_rows ошибок=$n_fail"
done
echo "$(date -u +%FT%TZ) p02-stage2-recompute готово: $*"
