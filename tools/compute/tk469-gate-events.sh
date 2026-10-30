#!/bin/bash
# TK-469: гейт events_b: флаг TK469_BARS_CACHE 0 (старое) vs 1 (кэш минут), группы g2 g3 g7 g32 на монете $1, 1 ядро. -> /data/tk0469/gate-events.out
sym=${1:-BTCUSDT}; W=/data/tk0461/bpev; PY=/data/tk421/venv/bin/python; O=/data/tk0469/ge; mkdir -p $O; cd $W
: > /data/tk0469/gate-events.out
for g in g2 g3 g7 g32; do for f in 0 1; do
  s=$(date +%s.%N)
  TK469_BARS_CACHE=$f taskset -c 15 $PY -I tools/compute/tk469_events_b.py $g docs/findings/bp-$g-events-2026-10-30.csv.gz /srv/alpha-sda/candles-1m/binance-um $O/$g-$f.csv.gz $sym >/dev/null 2>$O/$g-$f.err
  e=$(date +%s.%N); echo "$g flag=$f wall=$(echo "$e - $s" | bc)" >> /data/tk0469/gate-events.out
done
  $PY -I - <<PY >> /data/tk0469/gate-events.out
import pandas as pd
a=pd.read_csv("$O/$g-0.csv.gz"); b=pd.read_csv("$O/$g-1.csv.gz")
print("$g", "IDENTICAL" if a.equals(b) else "DIFF", len(a), len(b))
PY
done
echo END >> /data/tk0469/gate-events.out
