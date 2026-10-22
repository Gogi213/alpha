#!/usr/bin/env bash
# TK-289 П-18 шаг 4: F3 оси 5 (ttl, 8 точек = 8 кл. x 9 мес. = 72 хеш-мес.), бинарник tk0289c (ENTRY_TTL_SECS +8). Обёртка tk289-f3.sh (env R/PLANSRC/BIN).
export REASON="TK-289 П-18 F3 оси 5 (ttl 8 точек), pgo tk0289c" R=/data/tk0289/f3ttl PLANSRC=/data/tk0289/f3ttlplan BIN=/opt/alpha-compute/bin/alpha-tk0289c-pgo
exec bash /data/tk0289/f3.sh
