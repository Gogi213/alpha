#!/usr/bin/env bash
# TK-289: psim F3 П-18 (стопка + 6а + 6в, 82 кл. x 251 сутки, f3 rc0) -> /data/tk0289/f3/psim/closes-p14f1-cap{0,3}-<мес>.json, сводка f3-sum.csv (sum_closes_f1.py),
#   затем closes с монетой для сита (tk298-symclose.sh, psim cap 0). PM=9 (девять месяцев параллельно). Как f6v-psim.sh.
set -u
export PM=${PM:-9}
R=/data/tk0289/f3
bash /opt/alpha-launch/tools/launch/psim-launch.sh $R || exit $?
python3 /data/tk0231/sum_closes_f1.py $R/psim /data/tk0289/f3-sum.csv closes-p14f1 || exit 9
bash /data/launch/out/L-P15F1/tk298-symclose.sh $R || exit 10
[ -e $R/psim/sym.done ] || exit 11
