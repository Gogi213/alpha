#!/usr/bin/env bash
# TK-279 (П-17 Г-81, вариант Б): проба меры ворот — 8 клеток W{5,10,15,20,30,45,60,90} на множестве B1 (side=bid, btc4h_max, age=2700),
# entry_gate=cxl<W>qprobe (порог недостижим — ход B1 прежний), gate-probe.csv на сутки; r2, --tape-log 300 (пачка tape/cxl).
#   tk279-g81-probe.sh <каталог прохода> <файл суток "мес ГГГГ-ММ-ДД"> ; env BIN (r2), SCRIPTS (каталог day-cells.sh), NX, GT
set -uo pipefail
R=$1; DAYS=$2; S=${SCRIPTS:-/data/tk0231}; SRC=${SRC_PLAN:-/data/tk0231/plan}; NX=${NX:-1}
mkdir -p "$R/plan" "$R/out"; cp "$DAYS" "$R/days.txt"
: > "$R/plan/L.cells"; : > "$R/plan/L.args"
mons=(jan feb mar apr may jun jul aug sep)
for ym in $(awk '{print substr($2,1,7)}' "$R/days.txt" | sort -u); do
  : > "$R/plan/L-$ym.cells"; { echo "--entry-form ladder3x0..0.0409sw2"; echo "--entry-ttl-secs 1800"; echo "--exit-form chase1510"; } > "$R/plan/L-$ym.args"
  for w in 5 10 15 20 30 45 60 90; do
    echo "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800-chase1510 cxlprobe$w" >> "$R/plan/L-$ym.cells"
    echo "--set cxlprobe$w:age=2700,side=bid,btc4h_max=-44.55,entry_gate=cxl${w}qprobe" >> "$R/plan/L-$ym.args"
  done
  grep -vE '^--(set|exit-form|entry-form|entry-ttl-secs|early-exit-secs)' "$SRC/p14f1.args" | sort -u >> "$R/plan/L-$ym.args"
done
export PLAN=L P=$R/plan O=$R/out BIN GT=${GT:-3} TP=1
xargs -P "$NX" -L1 bash -c 'bash "$0/day-cells.sh" "run-$1-$2" "$1" "$2"' "$S" < "$R/days.txt"
