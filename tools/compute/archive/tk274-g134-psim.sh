#!/bin/bash
# TK-274 П-15 Г-134: psim (p12-r1-psim.py, образец tk251-r1col-psim.sh) по готовым клеткам /data/tk0274/g134-r1/cells (18 хешей × янв–сен, отбор r1cell --hist own) -> closes cap0/cap3 -> сводка sum_closes_r1.
# Выход: $W/ps/closes-cap{0,3}-<мес>.json, $W/closes-g134-sum.csv, $W/timing.txt, $W/rc.txt. Только gate, ≤ 3 ядер.
W=/data/tk0274/g134-psim; C=/data/tk0274/g134-r1/cells; MONS="jan feb mar apr may jun jul aug sep"; P=${PM:-3}
mkdir -p $W/ps/b && cd $W || exit 9; : > timing.txt; rm -f rc.txt
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
python3 /data/tk0251/p3/in/sum_closes_r1.py $W/ps $W/closes-g134-sum.csv > $W/sum-tot.txt 2>&1 || bad=1
echo "bad=$bad" > rc.txt; exit $bad
