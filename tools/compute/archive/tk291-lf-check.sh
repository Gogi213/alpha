#!/usr/bin/env bash
# TK-291 (П-19 §7 п.1): ворота 0 — честный bounce-grid с lot_first ∈ {0.5, 2, 8} на B1 за август и сентябрь (3 клетки × 2 мес.,
#   6 хеш-мес.) против линейного пересчёта по B1-проходу (tk291-b1-touch.sh) — сравнение: tk291-lin.py. Раннер day-cells.sh (--touch-col).
#   tk291-lf-check.sh prep   — план /data/tk291/plan (lf.cells, lf.args, пустые помесячные), сутки авг/сен
#   tk291-lf-check.sh run    — проход (NX исполнителей по GT ядер); выход /data/tk291/out-lf/run-<мес>-<сутки>/out/<сутки>/
set -u
D=/data/tk291; P=$D/plan
BASE='age=2700,side=bid,btc4h_max=-44.55'; F=ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800
case ${1:-} in
prep)
  mkdir -p "$P"
  : > $P/lf.cells; : > $P/lf.args
  printf '%s\n' '--deadline-secs 14400' '--entry-form ladder3x0..0.0409sw2' '--entry-ttl-secs 1800' '--stop-form pct2' '--take-form tr1x1' >> $P/lf.args
  for k in 0p5:0.5 2:2 8:8; do
    echo "$F t-bid-btc4h-q1-lf${k%%:*}" >> $P/lf.cells
    echo "--set t-bid-btc4h-q1-lf${k%%:*}:$BASE,lot_first=${k##*:}" >> $P/lf.args
  done
  for m in 08 09; do : > $P/lf-2026-$m.cells; : > $P/lf-2026-$m.args; done
  grep -E '^(aug|sep) ' /data/tk0231/days.txt > $D/days-lf.txt; wc -l < $D/days-lf.txt;;
run)
  export PLAN=lf P=$P O=$D/out-lf BIN=$D/alpha-new GT=${GT:-1} TP=1 REASON="tk291: ворота 0 lot_first, бинарь не pgo (3 клетки, авг+сен)"
  xargs -P "${NX:-3}" -L1 bash -c 'bash /data/tk291/day-cells.sh "run-$0-$1" "$0" "$1"' < $D/days-lf.txt
  touch $D/lf.done;;
esac
