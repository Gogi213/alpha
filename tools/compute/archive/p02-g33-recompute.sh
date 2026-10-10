#!/usr/bin/env bash
# Копия `~/alpha/tmp-p02w3/p02-g33-recompute.sh` Steam Deck, которой считался Г-33 (причинная §12) на авг/сен
# (обёртка `run-g33.sh`: JOIN=p02-g33-causal-join.py G36_RATIO=0 G33_MAX_TRADED=0). От `p02-wave3-wall-recompute.sh`
# отличается только заголовком компакта (`chain_len,chain_stop`). В репозиторий внесена TK-018 (июль).
# П-02, третья очередь (Г-33/Г-36): `lob levels` по пулу и суткам + join с
# кэшем касаний D20 (`p02-wave3-wall-join.py`, пороги заморожены заранее),
# со сжатием на лету. Полный levels-<SYM>.csv на диске не остаётся дольше
# одного символа-суток (В-106).
#
#   ALPHA_HOME=<эпоха> D20=<каталог кэша D20 эпохи> SRC_PREFIX=<префикс суточных корней> \
#   OUT=<куда класть сжатые сутки> G36_RATIO=<заморож. порог> [G33_MAX_TRADED=0] \
#   [BIN=bin/alpha-3a9fe23] [JOBS=3] [JOIN=tools/compute/p02-wave3-wall-join.py] \
#   tools/compute/p02-wave3-wall-recompute.sh <сутки...>
#
# Возобновляемо: сутки с файлом-меткой <OUT>/<день>.done пропускаются повторно.
set -euo pipefail
ALPHA_HOME="${ALPHA_HOME:?ALPHA_HOME обязателен}"
D20="${D20:?D20 обязателен}"
SRC_PREFIX="${SRC_PREFIX:?SRC_PREFIX обязателен}"
OUT="${OUT:?OUT обязателен}"
JOIN="${JOIN:?JOIN обязателен — путь к p02-wave3-wall-join.py}"
G36_RATIO="${G36_RATIO:?G36_RATIO обязателен (заморожен до счёта)}"
G33_MAX_TRADED="${G33_MAX_TRADED:-0}"
export BIN="${BIN:-bin/alpha-3a9fe23}"
JOBS="${JOBS:-3}"

cd "$ALPHA_HOME"
mkdir -p "$OUT"
COMPACT_HEADER="symbol,day_utc,side,price_tick,touch_index,ended_by_death,chain_len,chain_stop"

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
  export day G33_MAX_TRADED G36_RATIO JOIN
  outfile="$OUT/$day.csv"
  echo "$COMPACT_HEADER" > "$outfile.tmp"
  export tmpdir
  tmpdir=$(mktemp -d)
  syms=$(ls "$d20day"/touches-*.csv 2>/dev/null | sed -e 's#.*/touches-##' -e 's#\.csv$##')
  n_syms=$(echo "$syms" | grep -c . || true)
  echo "$syms" | xargs -r -P "$JOBS" -I{} bash -c '
    sym="$1"
    raw="$tmpdir/$sym-levels.csv"
    touches="$d20day/touches-$sym.csv"
    compact="$tmpdir/compact-$sym.csv"
    : > "$compact"
    if nice -n 15 "$BIN" lob levels --root "$src" --symbol "$sym" \
        --h3-mode notional --h3-usd 10000 \
        --out "$raw" > "$tmpdir/$sym.log" 2>&1; then
      if python3 "$JOIN" --symbol "$sym" --day-utc "$day" \
          --levels "$raw" --touches "$touches" --out "$compact" \
          --g33-max-traded "$G33_MAX_TRADED" --g36-ratio "$G36_RATIO" \
          >> "$tmpdir/$sym.log" 2>&1; then
        :
      else
        echo "$sym join-failed" >> "$tmpdir/failed.txt"
      fi
      rm -f "$raw"
    else
      echo "$sym levels-failed" >> "$tmpdir/failed.txt"
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
echo "$(date -u +%FT%TZ) p02-wave3-wall-recompute готово: $*"
