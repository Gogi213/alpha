#!/bin/bash
# TK-469: профиль tk461_bp_events_b.py на одной монете, 1 ядро (taskset -c 15). prof-events.sh <g> <SYM> -> /data/tk0469/prof-<g>-<SYM>.txt
g=$1; sym=$2; W=/data/tk0461/bpev; ev=$g; [ $g = g63 ] && ev=g63-out
cd $W; s=$(date +%s.%N)
taskset -c 15 /data/tk421/venv/bin/python -I -m cProfile -s cumtime tools/compute/tk461_bp_events_b.py $g docs/findings/bp-$ev-events-2026-10-30.csv.gz /srv/alpha-sda/candles-1m/binance-um /data/tk0469/prof-$g-$sym.csv.gz $sym > /data/tk0469/prof-$g-$sym.txt 2>&1
e=$(date +%s.%N); echo "wall $(echo "$e - $s" | bc)" >> /data/tk0469/prof-$g-$sym.txt
