#!/usr/bin/env bash
# TK-332 (П-22 §6): план клетки S0 для A (S0 на фьючерсных стенах) и B (спот-стены → фьюч): S0 = B1 дословно (usd_min=10000, без flow).
#   tk332-s0-plan.sh <каталог плана>   → s0.cells, s0.args, s0-2026-0{1..9}.{cells,args}; дальше PLAN=s0 P=<каталог> day-cells.sh (B — ещё SPOT_EPOCH, JRN спота)
set -eu
P=${1:?каталог плана}; mkdir -p "$P"
FORM='ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800'; SET='s0:age=2700,side=bid,btc4h_max=-44.55,usd_min=10000'
echo "$FORM s0" > "$P/s0.cells"
printf '%s\n' '--deadline-secs 14400' '--entry-form ladder3x0..0.0409sw2' '--entry-ttl-secs 1800' '--stop-form pct2' '--take-form tr1x1' "--set $SET" > "$P/s0.args"
for m in 01 02 03 04 05 06 07 08 09; do : > "$P/s0-2026-$m.cells"; : > "$P/s0-2026-$m.args"; done
