#!/bin/bash
# TK-303 Г-134 F2 (9 хешей frontrun_delta_10s_lots, lo, q 6…30): r1cell apply --hist own (пул tk064, как F1 TK-274) -> psim p12-r1-psim.py cap0/cap3 -> сводка sum_closes_r1.
# Спека: $W/g134f2.r1spec (r1cell.py spec --protocol P-15 --fraction 2 --hyp Г-134). Выход: $W/ps/closes-cap{0,3}-<мес>.json, $W/closes-g134f2-sum.csv, $W/rc.txt (bad=0 — ок). gate ≤ 3 ядер.
W=/data/tk0303/g134f2; C=$W/r1/cells; MONS="jan feb mar apr may jun jul aug sep"; P=${PM:-3}
mkdir -p $W/ps/b && cd $W || exit 9; : > timing.txt; rm -f rc.txt
python3 /opt/alpha-launch/tools/compute/r1cell.py apply --spec $W/g134f2.r1spec --pool /data/tk064/pool --hist own --out $W/r1 --months "${MONS// /,}" > apply.log 2>&1 \
  || { echo "apply rc" > rc.txt; exit 4; }
python3 - "$W/ps" "$C" <<'PY' || { echo "mk rc" > rc.txt; exit 3; }
import sys
s = open("/data/tk063r1/p12-r1-psim.py", encoding="utf-8").read()
s = s.replace('O = "/data/tk063r1"', f'O = "{sys.argv[1]}"').replace('CELLS = "/data/tk064/r1/cells"', f'CELLS = "{sys.argv[2]}"')
a = "        cell = os.path.basename(cf)[:-4]\n"
assert a in s
s = s.replace(a, a + "        if cell not in os.environ['R1KEEP'].split(','):\n            continue\n")
open(sys.argv[1] + ".py", "w", encoding="utf-8").write(s)
PY
ln -sfn /data/p12r2/v-b/study ps/b/study
one() { mo=$1; T1=$(date +%s)
  R1KEEP=$(ls $C/$mo | sed 's/\.csv$//' | paste -sd,) python3 $W/ps.py $mo > ps/run-$mo.log 2>&1; r=$?
  echo "psim $mo rc=$r s=$(( $(date +%s)-T1 )) клеток=$(ls $C/$mo | wc -l)" >> timing.txt; }
export -f one; export W C
echo $MONS | tr ' ' '\n' | xargs -P "$P" -I{} bash -c 'one {}'
bad=0; for m in $MONS; do [ -e ps/psim-$m.done ] && [ ! -e ps/fail-$m.txt ] || { echo "красный $m" >> timing.txt; bad=1; }; done
python3 /data/tk0251/p3/in/sum_closes_r1.py $W/ps $W/closes-g134f2-sum.csv > $W/sum-tot.txt 2>&1 || bad=1
echo "bad=$bad" > rc.txt; exit $bad
