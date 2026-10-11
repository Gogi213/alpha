#!/bin/bash
# TK-071 п.3: одна половина пары A=A: tk071-pair.sh <метка> <W> <PASSES> — instructions:u (perf) в /data/tk071/pairs/<метка>.perf
mkdir -p /data/tk071/pairs
perf stat -x, -e instructions:u -o /data/tk071/pairs/$1.perf python3 /data/tk071/tk071-pair.py "$2" "$3" > /data/tk071/pairs/$1.out
echo "end $(date +%s)" >> /data/tk071/pairs/$1.out
