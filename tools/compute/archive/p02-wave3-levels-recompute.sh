#!/usr/bin/env bash
# П-02, третья очередь (Г-33/Г-36): `lob levels` по пулу и суткам, со сжатием на
# лету — компактная строка на УРОВЕНЬ (не на касание): symbol,day_utc,side,
# price_tick,birth_ms,repriced,traded_lots,size_max,size_monotonic. Полный
# levels-<SYM>.csv на диске не остаётся дольше одного символа-суток (В-106).
#
# Пул суток/символов берётся из существующего кэша D20 (список файлов
# touches-*.csv) — тот же приём, что у `p02-stage2-recompute.sh` (вторая
# волна), гарантия «те же символы, что и в touches/approaches».
#
#   ALPHA_HOME=<эпоха> D20=<каталог кэша D20 эпохи> SRC_PREFIX=<префикс суточных корней> \
#   OUT=<куда класть сжатые сутки> [BIN=bin/alpha-3a9fe23] [JOBS=3] \
#   tools/compute/p02-wave3-levels-recompute.sh <сутки...>
#
# Возобновляемо: сутки с файлом-меткой <OUT>/<день>.done пропускаются повторно.
set -euo pipefail
ALPHA_HOME="${ALPHA_HOME:?ALPHA_HOME обязателен}"
D20="${D20:?D20 обязателен (каталог кэша с touches-*.csv для списка символов)}"
SRC_PREFIX="${SRC_PREFIX:?SRC_PREFIX обязателен (например study/root-)}"
OUT="${OUT:?OUT обязателен}"
export BIN="${BIN:-bin/alpha-3a9fe23}"
JOBS="${JOBS:-3}"

cd "$ALPHA_HOME"
mkdir -p "$OUT"
COMPACT_HEADER="symbol,day_utc,side,price_tick,birth_ms,repriced,traded_lots,size_max,size_monotonic"

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
{ print sym","$h["day_utc"]","$h["side"]","$h["price_tick"]","$h["birth_ms"]","$h["repriced"]","$h["traded_lots"]","$h["size_max"]","$h["size_monotonic"] }
AWK
  outfile="$OUT/$day.csv"
  echo "$COMPACT_HEADER" > "$outfile.tmp"
  syms=$(ls "$d20day"/touches-*.csv 2>/dev/null | sed -e 's#.*/touches-##' -e 's#\.csv$##')
  n_syms=$(echo "$syms" | grep -c . || true)
  echo "$syms" | xargs -r -P "$JOBS" -I{} bash -c '
    sym="$1"
    raw="$tmpdir/$sym.csv"
    if nice -n 15 "$BIN" lob levels --root "$src" --symbol "$sym" \
        --h3-mode notional --h3-usd 10000 \
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
echo "$(date -u +%FT%TZ) p02-wave3-levels-recompute готово: $*"
