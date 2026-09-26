#!/usr/bin/env bash
# Общие умолчания счётных скриптов (В-39: один путь вместо ~11 захардкоженных копий).
# Источник: source "$(dirname "${BASH_SOURCE[0]}")/_env.sh" — до первого `cd`, чтобы
# путь до этого файла резолвился от места скрипта, а не от текущего каталога.
#
# RUNS_JOURNAL — журнал испытаний (docs/plan/runs.csv не при чём, это счётный
# study/runs-<дата>.csv на копии дома — путь не менять, только имя переменной).
RUNS_JOURNAL="${RUNS_JOURNAL:-study/runs-2026-09-19.csv}"

# У3 (T-16, Судья 213fefb): общие константы прогона — одно определение вместо копий в скриптах. Через `:=`: заданное
# вызывающим в окружении не перетирается; имена с префиксом ALPHA_ не пересекаются с локальными (`RTT`, `USD`, …).
# Задержка — стандартная, В-68 (медиана и p95 place/cancel/taker, нс).
: "${ALPHA_RTT:=--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000}"
# Порог H3 касаний/уровней/сетки: по деньгам, $10 000.
: "${ALPHA_H3:=--h3-mode notional --h3-usd 10000}"
# Модель очереди — по назначению, двумя явными именами (условие Судьи 4): касания и ночная база — risk-adverse;
# подходы (D20), OOS, главный вариант и гейты подходов — prob:3 (F10).
: "${ALPHA_QUEUE_TOUCH:=--queue-model risk-adverse}"
: "${ALPHA_QUEUE_APPROACH:=--queue-model prob:3}"

# У2: реестр имён наборов — sets.txt рядом с этим файлом (одно имя — одно определение; проверка — sets-check.sh).
ALPHA_SETS_FILE="${ALPHA_SETS_FILE:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/sets.txt}"
# alpha_sets имя… → «имя:ключи …» через пробел (для SETS=…); неизвестное имя — строка в stderr и код 1.
alpha_sets() {
  local n d out=()
  for n in "$@"; do
    d=$(awk -v n="$n" 'index($0, n ":") == 1 { print substr($0, length(n) + 2); exit }' "$ALPHA_SETS_FILE")
    [ -n "$d" ] || { echo "alpha_sets: нет набора «$n» в $ALPHA_SETS_FILE" >&2; return 1; }
    out+=("$n:$d")
  done
  echo "${out[*]}"
}
# alpha_set_args имя… → «--set имя:ключи …» (строка аргументов `lob bounce-grid`).
alpha_set_args() {
  local s x
  s=$(alpha_sets "$@") || return 1
  for x in $s; do printf -- '--set %s ' "$x"; done
}
