#!/usr/bin/env bash
# TK-668: код на srv — плоская копия tools/p42/*.py, *.sh (без tests) + selection_bias.py → /data/p42-tk668/code, тесты → /data/p42-tk668/tests. Запуск с ПК, из корня репозитория.
set -euo pipefail
K=(-i /c/Users/Георгий/.ssh/id_rsa -o UserKnownHostsFile=/c/Users/Георгий/.ssh/known_hosts -o ConnectTimeout=15)
H=root@5.231.25.224
cd "$(dirname "$0")/../.."
T="${TEMP:-/tmp}"; command -v cygpath >/dev/null && T="$(cygpath -u "$T")"
S="$T/tk668-stage-$$"; rm -rf "$S"; mkdir -p "$S/code" "$S/tests"
cp tools/p42/*.py tools/p42/*.sh "$S/code/"; cp .claude/skills/alpha-research/scripts/selection_bias.py "$S/code/"
cp tools/p42/tests/test_tk668.py "$S/tests/" 2>/dev/null || true
ssh "${K[@]}" "$H" "mkdir -p /data/p42-tk668"
tar --force-local -C "$S" -cf - code tests | ssh "${K[@]}" "$H" "cd /data/p42-tk668 && tar -xf -"
rm -rf "$S"
echo synced
