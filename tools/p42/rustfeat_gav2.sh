#!/usr/bin/env bash
# TK-609: ворота G-A1 / G-A3 / G-A2 на `alpha-feat-v2` со складом суток (StoreDaySrc), один сквозной прогон на srv (юнит systemd-run, nice 0).
# Каждое окно считается дважды: проход «a» — холодный, со `--store-verify` (склад заполняется, каждые сутки сверены с разбором CSV; mismatch должен быть 0),
# проход «b» — тёплый, без сверки (боевой путь: декодер склада). Сравнение с эталоном Python — побитово, оба прохода.
# Использование: rustfeat_gav2.sh [BIN]; склады: ST1 (окна wa/wb/wc), ST_U0, ST_AUG — разные каталоги: каталоги сделок окон различаются (ключ файла склада — монета+дата+размер+mtime zip).
set -uo pipefail
BIN=${1:-/data/p42-tk609/bin/alpha-feat-v2}; R=/data/p42-tk609; SC="--store-cap-gb ${CAP:-40} --store-min-free-gb 30"
ST1=$R/store-feat; ST_U0=$R/store-feat-u0; ST_AUG=$R/store-feat-aug
export NICE=0 BIN
cd $R
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
log "G-A1 a (холодный, verify)"; OUT=$R/ga1-v2a P42_FEAT_ARGS="--store $ST1 $SC --store-verify" bash $R/ga1.sh > $R/ga1-v2a.log 2>&1
log "G-A1 b (тёплый)";          OUT=$R/ga1-v2b P42_FEAT_ARGS="--store $ST1 $SC" bash $R/ga1.sh > $R/ga1-v2b.log 2>&1
log "G-A3 (из таблиц G-A1 b)";  G1=$R/ga1-v2b OUT=$R/ga3-v2 P42_FEAT_ARGS="--store $ST1 $SC" bash $R/ga3.sh > $R/ga3-v2.log 2>&1
log "G-A2 deriv";
OUT=$R/ga2-v2 bash $R/ga2.sh u0 deriv 2 > $R/ga2-v2-u0-deriv.log 2>&1 &
OUT=$R/ga2-v2 bash $R/ga2.sh aug deriv 2 > $R/ga2-v2-aug-deriv.log 2>&1 &
wait
log "G-A2 flow a (холодный, verify)"
OUT=$R/ga2-v2a P42_FEAT_ARGS="--store $ST_U0 $SC --store-verify" bash $R/ga2.sh u0 flow 2 > $R/ga2-v2a-u0-flow.log 2>&1 &
OUT=$R/ga2-v2a P42_FEAT_ARGS="--store $ST_AUG $SC --store-verify" bash $R/ga2.sh aug flow 2 > $R/ga2-v2a-aug-flow.log 2>&1 &
wait
log "G-A2 flow b (тёплый)"
OUT=$R/ga2-v2b P42_FEAT_ARGS="--store $ST_U0 $SC" bash $R/ga2.sh u0 flow 2 > $R/ga2-v2b-u0-flow.log 2>&1 &
OUT=$R/ga2-v2b P42_FEAT_ARGS="--store $ST_AUG $SC" bash $R/ga2.sh aug flow 2 > $R/ga2-v2b-aug-flow.log 2>&1 &
wait
log "ГОТОВО"; echo done > $R/gav2.done
