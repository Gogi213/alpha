#!/usr/bin/env bash
# TK-609: ворота G-A1 / G-A3 / G-A2 на `alpha-feat` со СКЛАДОМ суток (StoreDaySrc), один сквозной прогон на srv (юнит systemd-run, nice 0, ≤ 4 процессов счёта).
# Склад — общий, один на все окна (предзаливка TK-607): `/data/p42-tk599/tick-store`. Включается окружением (`TK594_STORE*`), как в боевом запуске TK-599, —
# вызывающий код (`rustfeat_run.py` в `retitr_ftab`) не меняется.
# Использование: rustfeat_gav2.sh <a|b> [BIN] [STORE]   (SCOPE=sw — только G-A1 и G-A3; SCOPE=ga2 — только G-A2; по умолчанию all)
#   a — со `--store-verify` (каждые сутки сверены с разбором CSV, mismatch должен быть 0), склад пишется (потолок 40 ГБ, стоп при свободном < 40 ГБ — как у предзаливки):
#       промах → разбор zip → атомарная запись (tmp+rename) тем же кодом, что у движка; прогон заодно заполняет склад;
#   b — боевая конфигурация: без сверки, склад только читается (`TK594_STORE_CAP_GB=0`).
# Сравнение с эталоном Python — побитово, оба прохода. Каталоги выхода — ga*-<метка><фаза>, метка — `TAG` (по умолчанию v3).
set -uo pipefail
PH=${1:?a|b}; BIN=${2:-/data/p42-tk609/bin/alpha-feat-v3}; STORE=${3:-/data/p42-tk599/tick-store}; R=/data/p42-tk609; TAG=${TAG:-v3}
export TK594_STORE=$STORE
if [ "$PH" = a ]; then
  export TK594_STORE_CAP_GB=40 TK594_STORE_MIN_FREE_GB=40 P42_FEAT_ARGS="--store-verify"
else
  export TK594_STORE_CAP_GB=0 TK594_STORE_MIN_FREE_GB=0; unset P42_FEAT_ARGS
fi
export NICE=0 BIN
cd $R
log() { echo "[$(date -u +%H:%M:%S)] $*"; }
S=$TAG$PH
log "фаза $PH ($TAG): бинарь $BIN md5 $(md5sum "$BIN" | cut -c1-12), склад $STORE, cap ${TK594_STORE_CAP_GB} ГБ, аргументы '${P42_FEAT_ARGS:-}'"
if [ "${SCOPE:-all}" != ga2 ]; then
  log "G-A1";  OUT=$R/ga1-$S bash $R/ga1.sh > $R/ga1-$S.log 2>&1
  log "G-A3 (из таблиц G-A1)";  G1=$R/ga1-$S OUT=$R/ga3-$S bash $R/ga3.sh > $R/ga3-$S.log 2>&1
fi
[ "${SCOPE:-all}" = sw ] && { log "ГОТОВО $S (G-A1 + G-A3)"; echo done > $R/gav2-$S.done; exit 0; }
log "G-A2 deriv"
OUT=$R/ga2-$S bash $R/ga2.sh u0 deriv 2 > $R/ga2-$S-u0-deriv.log 2>&1 &
OUT=$R/ga2-$S bash $R/ga2.sh aug deriv 2 > $R/ga2-$S-aug-deriv.log 2>&1 &
wait
log "G-A2 flow"
OUT=$R/ga2-$S bash $R/ga2.sh u0 flow 2 > $R/ga2-$S-u0-flow.log 2>&1 &
OUT=$R/ga2-$S bash $R/ga2.sh aug flow 2 > $R/ga2-$S-aug-flow.log 2>&1 &
wait
log "ГОТОВО $S"; echo done > $R/gav2-$S.done
