#!/usr/bin/env bash
# TK-315 П-17 F2: psim (cap 0/3) по выходу p17f2 (383 клетки x 251 сутки, rc0) -> /data/tk0315/p17f2/psim/closes-p14f1-cap{0,3}-<мес>.json, сводка /data/tk0315/p17f2-sum.csv (sum_closes_f1.py). env PM (умолч. 3).
export PM=${PM:-3}
bash /opt/alpha-launch/tools/launch/psim-launch.sh /data/tk0315/p17f2 || exit $?
python3 /data/tk0231/sum_closes_f1.py /data/tk0315/p17f2/psim /data/tk0315/p17f2-sum.csv closes-p14f1
