#!/usr/bin/env bash
# TK-313, П-21 §3: план клеток спота (формат day-cells.sh: <PLAN>.cells / .args, помесячные пустые) — по образцу tk291-b1-touch.sh prep.
#   tk313-spot-plan.sh <каталог плана>     → <каталог>/spot.cells, spot.args, spot-2026-0{1..9}.{cells,args}
# Форма входа = B1 (ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800); набор = age=2700,side=bid,btc4h_max=-44.55 + flow=S + usd_min=U.
# S0 = B1 дословно (usd_min=10000, без flow). Решётка G = S{нет,25,50,100} × U{нет,u1,u2,u3} без (нет,нет) = 15 клеток.
# 17-я клетка (нет,нет) — контроль «пол вообще нужен ли», вне N_test (Холм по 16, П-21 §8; замечание Судьи п.2).
# u1..u3 — П-21 §10 п.2 (округление до $).
set -eu
P=${1:?каталог плана}; mkdir -p "$P"
FORM='ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800'
BASE='age=2700,side=bid,btc4h_max=-44.55'
U1=30943; U2=107397; U3=376218
: > "$P/spot.cells"; : > "$P/spot.args"
printf '%s\n' '--deadline-secs 14400' '--entry-form ladder3x0..0.0409sw2' '--entry-ttl-secs 1800' '--stop-form pct2' '--take-form tr1x1' >> "$P/spot.args"
add() { echo "$FORM $1" >> "$P/spot.cells"; echo "--set $1:$2" >> "$P/spot.args"; }
add s0 "$BASE,usd_min=10000"
for s in n 25 50 100; do
  for u in n 1 2 3; do
    [ "$s$u" = nn ] && continue
    spec=$BASE; [ "$s" = n ] || spec="$spec,flow=$s"
    case $u in 1) spec="$spec,usd_min=$U1";; 2) spec="$spec,usd_min=$U2";; 3) spec="$spec,usd_min=$U3";; esac
    add "g-s$s-u$u" "$spec"
  done
done
add ctl-nn "$BASE"
for m in 01 02 03 04 05 06 07 08 09; do : > "$P/spot-2026-$m.cells"; : > "$P/spot-2026-$m.args"; done
echo "клеток: $(wc -l < "$P/spot.cells")"
