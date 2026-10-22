#!/usr/bin/env bash
# TK-174: гейт r2-семей (r2, tk115*) — сборка головы с feature r2 (v3, без PGO: выходы от PGO не зависят) и gate-families.sh.
#   gate-r2.sh <тег> <W со снимком (push-head.sh)> <боевой бинарь головы> <семьи>
# Сборка — ТОЛЬКО через calc-build.sh r2 (TK-207: снимок==голова, сбой=rc≠0, хеш вшит); своей сборки здесь нет.
# Выход: /opt/alpha-compute/bin/alpha-<тег>-r2, /data/<тег>/gate2.out, маркер /data/<тег>/r2.done. Запуск — только через alsched.
set -uo pipefail
TAG=${1:?тег}; SW=${2:?W со снимком}; PGO=${3:?бинарь}; FAMS=${4:?семьи}; W=/data/$TAG
rm -f "$W/r2.done" "/opt/alpha-compute/bin/alpha-$TAG-r2"   # TK-219 п.11: прежний бинарь тега удалён до сборки
bash "$(dirname "$0")/calc-build.sh" "$SW" r2 || exit 1
install -D "$SW/alpha-new-r2" "/opt/alpha-compute/bin/alpha-$TAG-r2" || exit 1
GATE_TAG=${TAG}2 NEW_R2="/opt/alpha-compute/bin/alpha-$TAG-r2" bash "$(dirname "$0")/gate-families.sh" "$PGO" "$FAMS" > "$W/gate2.out" 2>&1
touch "$W/r2.done"
