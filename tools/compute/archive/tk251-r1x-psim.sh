#!/bin/bash
# TK-251 П2: psim по ГОТОВЫМ клеткам R1X (/data/tk202/r1x/cells/<мес>/<хеш>.csv, только чтение; сигналы/исходы не пересчитываются, В-213).
# Берёт канонический p12-r1-psim.py (/data/tk063r1; его эквивалентность r1ext проверена в TK-250) с подменой O/CELLS и фильтром R1KEEP.
#   tk251-r1x-psim.sh            пилот (2 хеша × янв, замер) -> полный проход янв–сен (PM месяцев параллельно, 3) -> сверка пилот == полный
# Выход: /data/tk0251/{pilot,ps}/closes-cap{0,3}-<мес>.json, /data/tk0251/timing.txt, /data/tk0251/rc.txt (итог). Только gate, ≤ 3 ядер.
W=/data/tk0251; CELLS=/data/tk202/r1x/cells; MONS="jan feb mar apr may jun jul aug sep"; P=${PM:-3}
mkdir -p $W/pilot $W/ps; cd $W || exit 9
mk() { # $1=O
  python3 - "$1" <<'PY' || exit 3
import sys
s = open("/data/tk063r1/p12-r1-psim.py", encoding="utf-8").read()
s = s.replace('O = "/data/tk063r1"', f'O = "{sys.argv[1]}"').replace('CELLS = "/data/tk064/r1/cells"', 'CELLS = "/data/tk202/r1x/cells"')
a = "        cell = os.path.basename(cf)[:-4]\n"
assert a in s
s = s.replace(a, a + "        if cell not in os.environ['R1KEEP'].split(','):\n            continue\n")
open(sys.argv[1] + ".py", "w", encoding="utf-8").write(s)
PY
}
mk $W/pilot; mk $W/ps
echo "# TK-251 П2 $(date +%F\ %T)" > timing.txt
PILOT=4966124ca46bf7f4,219742832b50aae1
for h in ${PILOT//,/ }; do [ -e $CELLS/jan/$h.csv ] || { echo "нет клетки $h" > rc.txt; exit 4; }; done
T0=$(date +%s)
R1KEEP=$PILOT python3 $W/pilot.py jan > pilot/run.log 2>&1; rc=$?
echo "pilot jan 2 хеша rc=$rc s=$(( $(date +%s)-T0 ))" >> timing.txt
[ $rc = 0 ] && [ ! -e pilot/fail-jan.txt ] && [ -e pilot/psim-jan.done ] || { echo "пилот красный rc=$rc" > rc.txt; exit 5; }
ln -sfn /data/p12r2/v-b/study ps/b/study 2>/dev/null || { mkdir -p ps/b; ln -sfn /data/p12r2/v-b/study ps/b/study; }
one() { mo=$1; T1=$(date +%s)
  R1KEEP=$(ls $CELLS/$mo | sed 's/\.csv$//' | paste -sd,) python3 $W/ps.py $mo > ps/run-$mo.log 2>&1; r=$?
  echo "full $mo rc=$r s=$(( $(date +%s)-T1 )) клеток=$(ls $CELLS/$mo | wc -l)" >> timing.txt; }
export -f one; export W CELLS
T2=$(date +%s)
echo $MONS | tr ' ' '\n' | xargs -P "$P" -I{} bash -c 'one {}'
echo "full всего s=$(( $(date +%s)-T2 ))" >> timing.txt
bad=0; for m in $MONS; do [ -e ps/psim-$m.done ] && [ ! -e ps/fail-$m.txt ] || { echo "красный $m" >> timing.txt; bad=1; }; done
python3 - >> timing.txt <<'PY'
import json
bad = 0
for cap in (0, 3):
    a = json.load(open(f"/data/tk0251/pilot/closes-cap{cap}-jan.json")); b = json.load(open(f"/data/tk0251/ps/closes-cap{cap}-jan.json"))
    for h in "4966124ca46bf7f4,219742832b50aae1".split(","):
        ok = a.get(h) == b.get(h) and a.get(h) is not None
        bad += not ok
        print("пилот==полный", f"cap{cap}", h, "OK" if ok else "DIFF")
print("расхождений пилот/полный", bad)
PY
echo "bad=$bad" > rc.txt; exit $bad
