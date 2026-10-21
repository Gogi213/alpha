#!/bin/bash
# TK-303 (П-15 F2): пороги Г-72 (лаги 1..118 + 78/91/104) и Г-146 (24 ч) для ВСЕХ целых q 1..99 (разрешимость 1, П-14 §56).
# Запуск только через alsched (1 ядро): lag — 279 файлов суток BTC, 24 ч — минутные свечи /data/tk046/<мес>.
# Скрипты analyze лежат рядом (копия на calc: /data/tk0303/). Выход /data/tk0303/thr/*.json, rc в thr.rc (ok).
set -euo pipefail
S="$(cd "$(dirname "$0")" && pwd)"
D=/data/tk0303/thr
mkdir -p "$D"
cd "$D"
python3 "$S/btc-lag-thresholds.py" --btc-seconds /data/tk204/btc-seconds --all-q --out btc-lag-thresholds-allq.json &
P1=$!
refs=()
for m in jan feb mar apr may jun jul aug sep oct; do refs+=(--ref "/data/tk046/$m/home/alpha/epochs/e-$m/study/regime/ref-BTCUSDT-1m.csv"); done
python3 "$S/btc-thresholds.py" "${refs[@]}" --from 2026-01 --all-q --out btc-thresholds-allq.json
wait $P1
echo ok > thr.rc
