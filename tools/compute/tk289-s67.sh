#!/usr/bin/env bash
# TK-289 П-18 ось 6в: распределение силы В-67 (S = size_at_arm / flow_1h_lots × 100) у сигналов B1 по месяцам, q10/25/50/75/90/95 -> /data/tk0289/s67/ (правило «шире/гуще» Судьи 19.10).
D=/data/tk0289/s67; mkdir -p $D; rm -f $D/done
python3 /data/tk0289/tk289-thr6b.py /data/tk0289/f1/out $D/s67.json $D/P-18-s67-dist.csv --s67 > $D/run.log 2>&1 || exit 1
echo ok > $D/done
