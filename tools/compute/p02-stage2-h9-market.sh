#!/usr/bin/env bash
# П-02, вторая волна, Г-86 (H9): рыночный вход при eaten_min=EATEN_MIN против лимитного (текущая
# лестница) на тех же событиях — обе клетки на `--signal touch` (обязательно: `eaten_min=`
# отклоняется движком при `--signal approach`, `src/commands/lob/bounce_grid/plan.rs:253-254`),
# поэтому база H9 — НЕ `b5/titrc-u500r` (тот на approach-сигнале), а отдельная пара клеток на
# одном и том же множестве касаний (фильтр `eaten_min` одинаков в обеих). Порог — только явно:
# `EATEN_MIN=89.9` (поправка §12, утверждена владельцем 26.09, В-113; прежний 99 давал 0 сигналов).
#
#   ALPHA_HOME=<эпоха> D20=<кэш D20 эпохи> SRC_PREFIX=<префикс суточных корней> \
#   CARRY_ROOT=<корень для --carry-root> OUT_BASE=<b5/... префикс> EATEN_MIN=<%> [BIN=bin/alpha-3a9fe23] \
#   [THREADS=3] tools/compute/p02-stage2-h9-market.sh <сутки...>
set -uo pipefail
ALPHA_HOME="${ALPHA_HOME:?}"
D20="${D20:?}"
SRC_PREFIX="${SRC_PREFIX:?}"
CARRY_ROOT="${CARRY_ROOT:?}"
OUT_BASE="${OUT_BASE:?}"
EATEN_MIN="${EATEN_MIN:?порог eaten_min — П-02 §12: 89.9}"
BIN="${BIN:-bin/alpha-3a9fe23}"
THREADS="${THREADS:-3}"
RTT="--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000"
SET="t-bid-btc4h-q1:age=2700,side=bid,btc4h_max=-44.55,eaten_min=$EATEN_MIN"

cd "$ALPHA_HOME"
for day in "$@"; do
  d20day="$D20/$day"
  src="${SRC_PREFIX}${day}"
  if [ ! -d "$d20day" ] || [ ! -d "$src" ]; then
    echo "$(date -u +%FT%TZ) $day: нет $d20day или $src — пропуск" >&2
    continue
  fi
  for cell in limit market; do
    out="$OUT_BASE-$cell/$day"
    if [ -f "$out.done" ]; then
      echo "$(date -u +%FT%TZ) $day/$cell: уже готово — пропуск"
      continue
    fi
    mkdir -p "$out"
    entry_form="ladder3x2..20w2"
    [ "$cell" = market ] && entry_form="market"
    nice -n 15 "$BIN" lob bounce-grid \
      --root "$src" --touches-from "$D20" --signal touch --queue-model prob:3 $RTT \
      --regime-from "${REGIME_FROM:-study/regime}" \
      --order-usd 500 --carry-root "$CARRY_ROOT" \
      --h3-mode notional --h3-usd 10000 \
      --entry-form "$entry_form" --entry-ttl-secs 1800 --band-exit-bps 20 \
      --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 --exit-form none \
      --set "$SET" --threads "$THREADS" --out-dir "$out" \
      > "$out.log" 2>&1
    touch "$out.done"
    n_rounds=$(tail -n +1 "$out"/*/rounds.csv 2>/dev/null | grep -vc "^#\|^symbol" || echo 0)
    echo "$(date -u +%FT%TZ) $day/$cell: rounds~=$n_rounds"
  done
done
echo "$(date -u +%FT%TZ) p02-stage2-h9-market готово: $*"
