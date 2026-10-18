#!/usr/bin/env bash
# TK-289: psim F1 П-18 ось 6в (70 клеток x 251 сутки, f6v rc0) -> /data/tk0289/f6v/psim/closes-p14f1-cap{0,3}-<мес>.json, сводка f6v-sum.csv (sum_closes_f1.py). PM=1. Как f1-psim.sh.
export PM=1
bash /opt/alpha-launch/tools/launch/psim-launch.sh /data/tk0289/f6v || exit $?
python3 /data/tk0231/sum_closes_f1.py /data/tk0289/f6v/psim /data/tk0289/f6v-sum.csv closes-p14f1
