#!/usr/bin/env bash
# Сетка форм на счётной машине (13.140.29.171, рядом с watcher/massedit):
# transient-сервис systemd с потолком памяти и низким CPU-весом, чтобы боевые
# сервисы соседей не заметили. Данные — /opt/alpha-compute/root (ночной rsync
# с коллектора, tools/sync-to-compute.sh), бинарник — /opt/alpha-compute/bin/alpha
# (сборка из /opt/alpha-compute/src, тот же HEAD, что в репозитории).
#
#   sudo tools/compute/run-grid.sh <метка> [аргументы bounce-grid...]
# пример:
#   sudo run-grid.sh 2026-09-17-usd50k --day 2026-09-17 --h3-mode notional --h3-usd 50000
# По умолчанию: RTT assumed 20 мс (В-37), лот от пула (22а), 3 потока из 4 vCPU
# (переопределить: THREADS=1 run-grid.sh …, чтобы гнать два прогона рядом),
# MemoryMax 4G (из 7.9 ГБ; соседи держат < 1 ГБ), CPUWeight 30, nice 15.
# F3 (20.09): модель очереди/исполнения — обязательный флаг сетки, умолчания в
# коде нет; здесь прежнее поведение — risk-adverse. Новая модель (prob:<n>,
# n — число предрегистрации) включается окружением: QUEUE_MODEL=prob:3
# run-grid.sh … — так её возьмёт ночь, не правя скрипт (F10).
# Артефакты: /opt/alpha-compute/b5/<метка>/{rounds.csv,forms.csv,manifest.txt},
# логи grid.out/grid.err там же; состояние — systemctl status alpha-grid-<метка>.
set -euo pipefail
LABEL="${1:?метка прогона}"; shift
OUT=/opt/alpha-compute/b5/$LABEL
mkdir -p "$OUT"
UNIT="alpha-grid-$LABEL"
systemctl reset-failed "$UNIT" 2>/dev/null || true
exec systemd-run --unit="$UNIT" --nice=15 \
  -p MemoryMax=4G -p MemorySwapMax=0 -p CPUWeight=30 \
  -p WorkingDirectory=/opt/alpha-compute \
  -p StandardOutput=append:"$OUT/grid.out" -p StandardError=append:"$OUT/grid.err" \
  -- /opt/alpha-compute/bin/alpha lob bounce-grid \
    --root /opt/alpha-compute/root \
    --queue-model "${QUEUE_MODEL:-risk-adverse}" \
    --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
    --order-qty-from-pool --threads "${THREADS:-3}" \
    --out-dir "$OUT" "$@"
