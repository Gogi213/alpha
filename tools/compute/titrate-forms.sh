#!/usr/bin/env bash
# Общий каркас титрования выхода лонга (план 2026-09-23, G9/G9b/G9c): замороженный вход F10 (подход
# D20, лестница, срок жизни входа 30 мин), сетка форм выхода поверх базы лонга и двух просадок BTC
# (1 ч / 4 ч, края v1, В-86) — обе эпохи («история», «запись») параллельно через oos-frozen.sh.
# Профили — тонкие обёртки: titrate-exit.sh (fix|trail|wall, В-87), titrate-gone.sh (gone, защита
# после снятия стены), titrate-be.sh (be, безубыток после снятия). Поведение и имена каталогов не
# менялись при выделении каркаса (2026-09-23) — прежние три скрипта делали то же самое каждый сам.
#
#   titrate-forms.sh <профиль fix|trail|wall|gone|be> <метка>
#
# Каталоги: fix|trail|wall → b5/titrx-<метка>-<профиль>; gone → b5/titrg-<метка>; be → b5/titrb-<метка>.
# Лог семьи: study/titrate-<exit|gone|be>-<метка>.log (общий у fix/trail/wall — три вызова одной
# метки в один лог, как раньше в titrate-exit.sh). gone/be требуют бинарник с формами `gone<W>tr<T>`/
# `be`/`bex` (BIN=bin/alpha-<хеш>) — без него `oos-frozen.sh` не поймёт форму выхода.
set -uo pipefail
PROFILE="${1:?профиль fix|trail|wall|gone|be}"
TAG="${2:?метка}"
SELF_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck source=_env.sh
source "$SELF_DIR/_env.sh"
A="${ALPHA_BASE:-$HOME/alpha}"
# TRIALS="" — пересчёт тех же испытаний (исправление бэктеста, не новые формы): в журнал не пишутся.
HIST="${HIST_HOME:-$A/epochs/e-archive}"

case "$PROFILE" in
  fix|trail|wall) FAMILY=exit; DIR="b5/titrx-$TAG-$PROFILE" ;;
  gone)           FAMILY=gone; DIR="b5/titrg-$TAG" ;;
  be)             FAMILY=be;   DIR="b5/titrb-$TAG" ;;
  *) echo "неизвестный профиль: $PROFILE (fix|trail|wall|gone|be)" >&2; exit 1 ;;
esac
LOG="$A/study/titrate-$FAMILY-$TAG.log"
say() { echo "== $(date -u +%FT%TZ) titrate-$FAMILY $TAG ($PROFILE): $*" | tee -a "$LOG"; }

case "$PROFILE" in
  gone|be) BIN="${BIN:?бинарник с gone<W>tr<T> — BIN=bin/alpha-<хеш>}" ;;
esac

SETS=$(tr ' ' '\n' < "$A/study/titration-sets-v1.txt" \
  | grep -E '^(t-bid-age-45|t-bid-btc1h-q1|t-bid-btc4h-q1):' | tr '\n' ' ')
[ "$(echo "$SETS" | wc -w)" -eq 3 ] || { say "наборы v1 не найдены"; exit 1; }

DL="--deadline-secs 3600 --deadline-secs 7200 --deadline-secs 14400"
case "$PROFILE" in
  fix)
    # Стоп × тейк раздельно, шаг 0.25 % (147 форм: стоп 0.5…2 % × тейк 0.5…2 % × дедлайн 1/2/4 ч).
    FORM_EXIT="$DL --exit-form none"
    for x in 0.5 0.75 1 1.25 1.5 1.75 2; do FORM_EXIT="$FORM_EXIT --stop-form pct$x --take-form tk$x"; done
    ;;
  trail)
    # Трейл вместо тейка: активация 0.5/1/1.5/2 % × откат 0.25/0.5/1 % (откат ≤ активации, 11 вариантов)
    # × стоп 1/1.5/2 % × дедлайн 1/2/4 ч (99 форм) — трейл на трёх стопах, не семи, ради времени счёта.
    FORM_EXIT="$DL --exit-form none --stop-form pct1 --stop-form pct1.5 --stop-form pct2"
    for t in tr0.5x0.25 tr0.5x0.5 tr1x0.25 tr1x0.5 tr1x1 tr1.5x0.25 tr1.5x0.5 tr1.5x1 tr2x0.25 tr2x0.5 tr2x1; do
      FORM_EXIT="$FORM_EXIT --take-form $t"
    done
    ;;
  wall)
    # Выход «съели 20 %» (В-80) при стопе 1/2 % и тейке 1:1 × дедлайн 1/2/4 ч (6 форм).
    FORM_EXIT="$DL --exit-form eat20 --stop-form pct1 --stop-form pct2 --take-form 1to1"
    ;;
  gone)
    # Стоп 2 % × тейк {1.75 %; трейл 1 %/откат 1 %} × удержание 2/4 ч × защита {нет; выход сразу
    # при снятии 90 %; трейл после снятия 50/90 % стены с откатом 0.25/0.5/1 %} = 32 формы.
    FORM_EXIT="--stop-form pct2 --take-form tk1.75 --take-form tr1x1 --deadline-secs 7200 --deadline-secs 14400"
    for x in none gone90 gone50tr0.25 gone50tr0.5 gone50tr1 gone90tr0.25 gone90tr0.5 gone90tr1; do
      FORM_EXIT="$FORM_EXIT --exit-form $x"
    done
    ;;
  be)
    # Стоп 2 % × тейк {трейл 1 %/откат 1 %; ранний трейл 0.5 %/0.25 %; тейк 1.75 %} × удержание 2/4 ч ×
    # защита {нет; безубыток после снятия 50/90 % стены — мягкий be и жёсткий bex} = 30 форм.
    FORM_EXIT="--stop-form pct2 --take-form tr1x1 --take-form tr0.5x0.25 --take-form tk1.75 --deadline-secs 7200 --deadline-secs 14400"
    for x in none gone50be gone50bex gone90be gone90bex; do
      FORM_EXIT="$FORM_EXIT --exit-form $x"
    done
    ;;
esac

say "прогон: история и запись"
declare -A PIDS
for epoch in "$HIST:2026-09-01" "$A:2026-09-16"; do
  home="${epoch%%:*}"; from="${epoch##*:}"
  errlog="$A/study/titrate-$FAMILY-$TAG-$PROFILE-$(basename "$home").err"
  : > "$errlog"
  # BIN/DAY_JOBS — только у gone/be (нужен бинарник с формами защиты; DAY_JOBS=4 умолчание там же
  # было раньше). fix/trail/wall не задают их вовсе — как в прежнем titrate-exit.sh: BIN и DAY_JOBS
  # берёт `oos-frozen.sh` сам (наследуя окружение вызывающего или считая по nproc).
  ENVS=(ALPHA_HOME="$home" FROM_DAY="$from" OOS_DIR="$DIR" SETS="$SETS" FORM_EXIT="$FORM_EXIT" \
        FORM_NAME=all VERDICT_FLAGS="${TRIALS---log-trials}" RUNS="${RUNS:-$RUNS_JOURNAL}" \
        GRID_THREADS="${GRID_THREADS:-2}")
  case "$PROFILE" in gone|be) ENVS+=(BIN="$BIN" DAY_JOBS="${DAY_JOBS:-4}") ;; esac
  env "${ENVS[@]}" "$A/bin/oos-frozen.sh" > "$errlog" 2>&1 &
  PIDS["$home"]=$!
done
fail=0
for home in "${!PIDS[@]}"; do
  if ! wait "${PIDS[$home]}"; then
    fail=$((fail + 1))
    say "ОШИБКА: прогон $home не завершился — $A/study/titrate-$FAMILY-$TAG-$PROFILE-$(basename "$home").err"
  fi
done
[ "$fail" -eq 0 ] || say "$fail из ${#PIDS[@]} прогонов провалились ($PROFILE)"
say "готово → $DIR"
