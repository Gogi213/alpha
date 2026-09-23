#!/usr/bin/env bash
# Пересчёт после исправления полуночи (23.09: позиция, открытая к концу суток UTC, выпадала из итога; теперь
# сутки дочитывают следующие — `--carry-root` в oos-frozen.sh). Все прогоны дашборда «Лонг в просадке» заново с
# новой меткой (старые остаются рядом для сравнения) и обвал 10–11.10.2025. Испытания те же — в журнал не пишутся.
#   BIN=bin/alpha-<хеш> recompute-carry.sh [метка]        # умолчание c1
set -uo pipefail
A="${ALPHA_BASE:-$HOME/alpha}"
TAG="${1:-c1}"
export BIN="${BIN:?бинарник с --carry-root — BIN=bin/alpha-<хеш>}"
export TRIALS=""
LOG="$A/study/recompute-carry-$TAG.log"
say() { echo "== $(date -u +%FT%TZ) recompute-carry $TAG: $*" | tee -a "$LOG"; }
say "выход (фикс / трейл / стена), $BIN"; "$A/bin/titrate-exit.sh" "$TAG"
say "реакция на снятие стены";          "$A/bin/titrate-gone.sh" "$TAG"
say "безубыток после снятия";           "$A/bin/titrate-be.sh" "$TAG"
say "обвал 10–11.10.2025";              CRASH_RUN="b5/crash-$TAG" "$A/bin/crash-stress.sh"
say "готово"
