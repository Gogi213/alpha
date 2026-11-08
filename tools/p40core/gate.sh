#!/bin/bash
# TK-500: гейт приёмки с любой машины — запускает srv-gate.sh на 5.231.25.224 (тесты, байт в байт, сверка с эталоном); код ≠ 0 — отказ.
# Запуск: bash tools/p40core/gate.sh [база=old]; на ПК только ssh (ключ и алиас srv — ~/.ssh/config).
d=$(dirname "$0")
ssh -o BatchMode=yes srv 'cat > /root/p40core/srv-gate.sh' < "$d/srv-gate.sh" || exit 2
exec ssh -o BatchMode=yes srv "bash /root/p40core/srv-gate.sh ${1:-old}"
