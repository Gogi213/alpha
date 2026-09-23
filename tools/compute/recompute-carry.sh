#!/usr/bin/env bash
# Пересчёт после двух исправлений 23.09: (1) позиция, открытая к концу суток UTC, выпадала из итога — теперь сутки
# дочитывают следующие (`CARRY` в oos-frozen.sh); (2) бэктест торговал минимальным лотом (~$5), а доллары в отчётах
# были процентом × $1000 — теперь позиция задаётся (`ORDER`, владелец: «объём позиции 500, полный»).
# Все прогоны дашборда «Лонг в просадке» заново с новой меткой (старые остаются рядом для сравнения) и обвал
# 10–11.10.2025. Испытания те же — в журнал не пишутся.
#   BIN=bin/alpha-<хеш> [ORDER="--order-usd 500"] recompute-carry.sh [метка]        # умолчание u500
set -uo pipefail
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_env.sh
source "$SELF_DIR/_env.sh"
A="${ALPHA_BASE:-$HOME/alpha}"
TAG="${1:-u500}"
export BIN="${BIN:?бинарник с --carry-root — BIN=bin/alpha-<хеш>}"
export ORDER="${ORDER:---order-usd 500}"
export TRIALS=""
LOG="$A/study/recompute-carry-$TAG.log"
say() { echo "== $(date -u +%FT%TZ) recompute-carry $TAG: $*" | tee -a "$LOG"; }
HIST="${HIST_HOME:-$A/epochs/e-archive}"
# Последние сутки «истории» (15.09) дочитываются первыми сутками записи (16.09, наш поток .50) — только чтобы
# закрыть позиции, открытые к полуночи; новых сигналов из них нет.
for f in "$A"/root/*-2026-09-16*.binlog; do [ -e "$HIST/root/$(basename "$f")" ] || ln -s "$f" "$HIST/root/"; done
SETS=$(tr ' ' '\n' < "$A/study/titration-sets-v1.txt" \
  | grep -E '^(t-bid-age-45|t-bid-btc1h-q1|t-bid-btc4h-q1):' | tr '\n' ' ')
CAND="--stop-form pct2 --take-form tr1x1 --take-form tr0.5x0.25 --take-form tk1.75 --deadline-secs 14400 --exit-form none"
say "кандидат (3 формы × 3 набора) и обвал, $ORDER, $BIN"
for epoch in "$HIST:2026-09-01" "$A:2026-09-16"; do
  ALPHA_HOME="${epoch%%:*}" FROM_DAY="${epoch##*:}" OOS_DIR="b5/titrc-$TAG" SETS="$SETS" FORM_EXIT="$CAND" \
    FORM_NAME=all VERDICT_FLAGS= RUNS="${RUNS:-$RUNS_JOURNAL}" GRID_THREADS="${GRID_THREADS:-2}" \
    DAY_JOBS="${DAY_JOBS:-2}" "$A/bin/oos-frozen.sh" > /dev/null 2>&1 &
done
wait
# Обвал — отдельно: сутки 10.10 держат до 11 ГБ, параллельно с эпохами падали по памяти (23.09).
CRASH_RUN="b5/crash-$TAG" DAY_JOBS=1 GRID_THREADS=4 "$A/bin/crash-stress.sh"
say "кандидат готов → b5/titrc-$TAG (история и запись), epochs/e-crash/b5/crash-$TAG"
say "выход (фикс / трейл / стена)";      "$A/bin/titrate-exit.sh" "$TAG"
say "реакция на снятие стены";           "$A/bin/titrate-gone.sh" "$TAG"
say "безубыток после снятия";            "$A/bin/titrate-be.sh" "$TAG"
say "готово"
