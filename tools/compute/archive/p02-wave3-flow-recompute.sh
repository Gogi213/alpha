#!/usr/bin/env bash
# П-02, третья очередь (Г-46): `lob trades` по пулу и суткам + окна CVD/цены
# до касания (`p02-wave3-flow-join.py`, окна 60с/300с — заданы протоколом,
# не калибруются), со сжатием на лету. Лента сделок на диске не остаётся
# дольше одного символа-суток (В-106); касания читаются из существующего
# кэша D20 (touches-<SYM>.csv), не пересчитываются.
#
#   ALPHA_HOME=<эпоха> D20=<каталог кэша D20 эпохи> SRC_PREFIX=<префикс суточных корней> \
#   OUT=<куда класть сжатые сутки> [BIN=bin/alpha-3a9fe23] [JOBS=3] \
#   [JOIN=tools/compute/p02-wave3-flow-join.py (абсолютный путь на машине счёта)] \
#   tools/compute/p02-wave3-flow-recompute.sh <сутки...>
#
# Возобновляемо: сутки с файлом-меткой <OUT>/<день>.done пропускаются повторно.
set -euo pipefail
ALPHA_HOME="${ALPHA_HOME:?ALPHA_HOME обязателен}"
D20="${D20:?D20 обязателен (каталог кэша с touches-*.csv)}"
SRC_PREFIX="${SRC_PREFIX:?SRC_PREFIX обязателен (например study/root-)}"
OUT="${OUT:?OUT обязателен}"
JOIN="${JOIN:?JOIN обязателен — путь к p02-wave3-flow-join.py}"
export BIN="${BIN:-bin/alpha-3a9fe23}"
JOBS="${JOBS:-3}"

cd "$ALPHA_HOME"
mkdir -p "$OUT"
COMPACT_HEADER="symbol,day_utc,side,price_tick,touch_index,ended_by_death,mismatch60,mismatch300"

for day in "$@"; do
  export d20day="$D20/$day"
  export src="${SRC_PREFIX}${day}"
  if [ ! -d "$d20day" ] || [ ! -d "$src" ]; then
    echo "$(date -u +%FT%TZ) $day: нет $d20day или $src — сутки пропущены" >&2
    continue
  fi
  if [ -f "$OUT/$day.done" ]; then
    echo "$(date -u +%FT%TZ) $day: уже готово (метка .done) — пропуск"
    continue
  fi
  export day
  outfile="$OUT/$day.csv"
  echo "$COMPACT_HEADER" > "$outfile.tmp"
  export tmpdir
  tmpdir=$(mktemp -d)
  syms=$(ls "$d20day"/touches-*.csv 2>/dev/null | sed -e 's#.*/touches-##' -e 's#\.csv$##')
  n_syms=$(echo "$syms" | grep -c . || true)
  echo "$syms" | xargs -r -P "$JOBS" -I{} bash -c '
    sym="$1"
    raw="$tmpdir/$sym-trades.csv"
    touches="$d20day/touches-$sym.csv"
    compact="$tmpdir/compact-$sym.csv"
    : > "$compact"
    if nice -n 15 "$BIN" lob trades --root "$src" --symbol "$sym" \
        --out "$raw" > "$tmpdir/$sym.log" 2>&1; then
      if python3 "$JOIN" --symbol "$sym" --day-utc "$day" \
          --trades "$raw" --touches "$touches" --out "$compact" \
          --windows-ms 60000,300000 >> "$tmpdir/$sym.log" 2>&1; then
        :
      else
        echo "$sym join-failed" >> "$tmpdir/failed.txt"
      fi
      rm -f "$raw"
    else
      echo "$sym trades-failed" >> "$tmpdir/failed.txt"
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
echo "$(date -u +%FT%TZ) p02-wave3-flow-recompute готово: $*"
