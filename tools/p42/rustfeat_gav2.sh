#!/usr/bin/env bash
# TK-609: ворота G-A1 / G-A3 / G-A2 на `alpha-feat-v2` со СКЛАДОМ суток (StoreDaySrc) — один сквозной прогон на srv (юнит systemd-run, nice 0, ≤ 4 процессов счёта).
# Склад — общий (по приказу владельца: одна общая предзаливка TK-607); здесь он читается ТОЛЬКО на чтение (`--store-cap-gb 0`): промах — разбор zip, запись не идёт.
# Использование: rustfeat_gav2.sh <a|b> [STORE] [BIN]
#   a — со `--store-verify`: каждые сутки сверены с разбором CSV (mismatch должен быть 0); b — без сверки (боевой путь: декодер склада).
# Сравнение с эталоном Python — побитово, оба прохода. Каталоги выхода — ga*-v2<фаза>.
set -uo pipefail
PH=${1:?a|b}; STORE=${2:-/data/p42-tk599/tick-store}; BIN=${3:-/data/p42-tk609/bin/alpha-feat-v2}; R=/data/p42-tk609
VER=""; [ "$PH" = a ] && VER="--store-verify"
ARGS="--store $STORE --store-cap-gb 0 --store-min-free-gb 0 $VER"
export NICE=0 BIN
cd $R
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
log "фаза $PH: склад $STORE, аргументы: $ARGS"
log "G-A1";  OUT=$R/ga1-v2$PH P42_FEAT_ARGS="$ARGS" bash $R/ga1.sh > $R/ga1-v2$PH.log 2>&1
log "G-A3 (из таблиц G-A1)";  G1=$R/ga1-v2$PH OUT=$R/ga3-v2$PH P42_FEAT_ARGS="$ARGS" bash $R/ga3.sh > $R/ga3-v2$PH.log 2>&1
log "G-A2 deriv"
OUT=$R/ga2-v2$PH bash $R/ga2.sh u0 deriv 2 > $R/ga2-v2$PH-u0-deriv.log 2>&1 &
OUT=$R/ga2-v2$PH bash $R/ga2.sh aug deriv 2 > $R/ga2-v2$PH-aug-deriv.log 2>&1 &
wait
log "G-A2 flow"
OUT=$R/ga2-v2$PH P42_FEAT_ARGS="$ARGS" bash $R/ga2.sh u0 flow 2 > $R/ga2-v2$PH-u0-flow.log 2>&1 &
OUT=$R/ga2-v2$PH P42_FEAT_ARGS="$ARGS" bash $R/ga2.sh aug flow 2 > $R/ga2-v2$PH-aug-flow.log 2>&1 &
wait
log "ГОТОВО фаза $PH"; echo done > $R/gav2$PH.done
