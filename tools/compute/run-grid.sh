#!/usr/bin/env bash
# Сетка форм на счётной машине: transient-сервис systemd с потолком памяти и низким
# CPU-весом, чтобы соседние сервисы не заметили. Данные — $ALPHA_HOME/root (ночной
# перенос с коллектора: push `tools/sync-to-compute.sh` на VPS-счётной, pull
# `tools/sync-from-collector.sh` на деке), бинарник — $ALPHA_HOME/bin/alpha (сборка из
# $ALPHA_HOME/src, тот же HEAD, что в репозитории).
# Машина задаётся окружением (L8, 2026-09-22): ALPHA_HOME — умолчание /opt/alpha-compute
# (VPS-счётная 13.140.29.171, root, системные юниты); на Steam Deck ALPHA_HOME=$HOME/alpha,
# пользователь `deck` без sudo ⇒ пользовательские юниты (`--user` выбирается по id).
#
#   sudo tools/compute/run-grid.sh <метка> [аргументы bounce-grid...]
# пример:
#   sudo run-grid.sh 2026-09-17-usd50k --day 2026-09-17 --h3-mode notional --h3-usd 50000
# По умолчанию: задержка измеренная (В-68), лот от пула (22а), потоки THREADS (3 на VPS,
# на деке 6 из 8), память GRID_MEM (4G на VPS из 7.9 ГБ; на деке 8G из 14), CPUWeight 30,
# nice 15.
# F3 (20.09): модель очереди/исполнения — обязательный флаг сетки, умолчания в
# коде нет; здесь прежнее поведение — risk-adverse. Новая модель (prob:<n>,
# n — число предрегистрации) включается окружением: QUEUE_MODEL=prob:3
# run-grid.sh … — так её возьмёт ночь, не правя скрипт (F10).
# Артефакты: $ALPHA_HOME/b5/<метка>/{rounds.csv,forms.csv,manifest.txt},
# логи grid.out/grid.err там же; состояние — systemctl status alpha-grid-<метка>.
set -euo pipefail
LABEL="${1:?метка прогона}"; shift
ALPHA_HOME="${ALPHA_HOME:-/opt/alpha-compute}"
OUT=$ALPHA_HOME/b5/$LABEL
mkdir -p "$OUT"
UNIT="alpha-grid-$LABEL"
SCOPE=(); [ "$(id -u)" = 0 ] || SCOPE=(--user)   # без root — пользовательские юниты (дек)
systemctl "${SCOPE[@]}" reset-failed "$UNIT" 2>/dev/null || true
SLICE=(); [ -n "${GRID_SLICE:-}" ] && SLICE=(--slice="$GRID_SLICE")   # общий потолок памяти ночи (дек)
exec systemd-run "${SCOPE[@]}" "${SLICE[@]}" --unit="$UNIT" --nice=15 \
  -p MemoryMax="${GRID_MEM:-4G}" -p MemorySwapMax=0 -p CPUWeight=30 \
  -p WorkingDirectory="$ALPHA_HOME" \
  -p StandardOutput=append:"$OUT/grid.out" -p StandardError=append:"$OUT/grid.err" \
  -- "$ALPHA_HOME/bin/alpha" lob bounce-grid \
    --root "$ALPHA_HOME/root" \
    --queue-model "${QUEUE_MODEL:-risk-adverse}" \
    --median-rtt-ns place=4200000,cancel=3980000,taker=5650000 --p95-rtt-ns place=4790000,cancel=4550000,taker=6420000 \
    --order-qty-from-pool --threads "${THREADS:-3}" \
    --out-dir "$OUT" "$@"
