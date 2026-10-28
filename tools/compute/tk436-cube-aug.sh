#!/bin/bash
# TK-436 (возврат Судьи п.2): куб сделок Bybit linear за август (сырьё /data/tk420/tr/bybit, 50 инстр., 1191 файл) — тот же tk436-cube.py trades, что и сентябрь (гейт сентября IDENTICAL). Июль: сырья сделок Bybit на диске нет.
cd /data/tk0436
PY=/data/tk421/venv/bin/python3
rm -rf cube3t_aug; mkdir cube3t_aug; rm -f cube3t_aug.rc
s=$(date +%s.%N)
$PY tk436-cube.py trades 2026-08 /data/tk420/tr/bybit cube3t_aug 1 > cube3t_aug/cube.log 2>&1
rc=$?
e=$(date +%s.%N)
echo "куб сделок авг. $(echo "$e - $s" | bc) s; rc $rc" > cube3t_aug/time.txt
echo $rc > cube3t_aug.rc
