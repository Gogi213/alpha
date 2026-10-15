#!/bin/bash
# TK-251 П3: R1COL «к счёту» (161 хеш; prep -> r1col.r1spec 149 колонок + r1col-comp.csv 12 композитов) × янв–сен: отбор -> клетки -> psim -> сводка closes.
# Вход (scp): $W/in/{r1cell.py,sum_closes_r1.py,r1col.r1spec,r1col-comp.csv}. Колонки: r1cell.py apply (пул /data/tk064/pool). Композиты g64/g73/g83: фев–сен готовые
# r1ext (/data/tk189/r1ext/cells, В-213), январь — tk064-r1cells.py apply --jan-self (кода композитов в r1cell.py нет). Сверка с r1ext (фев–сен, md5) — verify.txt.
# Выход: $W/{cells,ps}/…, $W/ps/closes-cap{0,3}-<мес>.json, $W/closes-r1col-sum.csv, $W/timing.txt, $W/rc.txt. Только gate, ≤ 3 ядер.
W=/data/tk0251/p3; IN=$W/in; MONS="jan feb mar apr may jun jul aug sep"; P=${PM:-3}; EXT=/data/tk189/r1ext/cells
cd $W || exit 9; : > timing.txt; rm -f rc.txt
st() { echo "$1 s=$(( $(date +%s)-T ))" >> timing.txt; T=$(date +%s); }
T=$(date +%s)
python3 $IN/r1cell.py apply --spec $IN/r1col.r1spec --pool /data/tk064/pool --out $W/apply --months ${MONS// /,} > apply.log 2>&1 || { echo "apply rc" > rc.txt; exit 3; }
st "apply колонки 149 × 9 мес"
python3 /data/tk189/tk064-r1cells.py apply --pool /data/tk064/pool --out $W/comp-jan --levels 95,90,80,67,50,33,20,10,5 --jan-self > comp-jan.log 2>&1 || { echo "comp-jan rc" > rc.txt; exit 3; }
st "композиты январь (jan-self)"
mkdir -p cells; for m in $MONS; do mkdir -p cells/$m; cp apply/cells/$m/*.csv cells/$m/; done
python3 - <<'PY' || { echo "сборка композитов" > rc.txt; exit 3; }
import csv, shutil, os
W = "/data/tk0251/p3"
for r in csv.DictReader(open(W + "/in/r1col-comp.csv", encoding="utf-8")):
    for m in "jan feb mar apr may jun jul aug sep".split():
        src = (f"{W}/comp-jan/cells/jan/" if m == "jan" else f"/data/tk189/r1ext/cells/{m}/") + r["name"] + ".csv"
        shutil.copyfile(src, f"{W}/cells/{m}/{r['hash']}.csv")
PY
st "сборка клеток"
python3 - > verify.txt <<'PY'
import csv, hashlib, sys
sys.path.insert(0, "/data/tk0251/p3/in"); import r1cell as R
W = "/data/tk0251/p3"; ok = bad = miss = 0; badl = []
md5 = lambda p: hashlib.md5(open(p, "rb").read()).hexdigest()
for r in csv.DictReader(open(W + "/in/r1col.r1spec", encoding="utf-8")):
    names = [R.r1ext_name(f, r["col"], r["keep_q"]) for f, (cols, sg) in R.FAM.items() if r["col"] in cols and sg == r["sign"]]
    for m in "feb mar apr may jun jul aug sep".split():
        mine = md5(f"{W}/cells/{m}/{r['hash']}.csv")
        ref = [f"/data/tk189/r1ext/cells/{m}/{n}.csv" for n in names]
        try: refm = {md5(p) for p in ref if __import__('os').path.exists(p)}
        except OSError: refm = set()
        if not refm: miss += 1
        elif mine in refm: ok += 1
        else: bad += 1; badl.append((r["hash"], r["col"], r["keep_q"], m))
print("r1ext сверка (колонки, фев–сен): равны", ok, "расходятся", bad, "нет референса", miss)
for x in badl[:20]: print("DIFF", x)
PY
st "сверка r1ext"
mk() { # $1=O
  python3 - "$1" <<'PY' || exit 3
import sys
s = open("/data/tk063r1/p12-r1-psim.py", encoding="utf-8").read()
s = s.replace('O = "/data/tk063r1"', f'O = "{sys.argv[1]}"').replace('CELLS = "/data/tk064/r1/cells"', 'CELLS = "/data/tk0251/p3/cells"')
a = "        cell = os.path.basename(cf)[:-4]\n"
assert a in s
s = s.replace(a, a + "        if cell not in os.environ['R1KEEP'].split(','):\n            continue\n")
open(sys.argv[1] + ".py", "w", encoding="utf-8").write(s)
PY
}
mkdir -p pilot ps; mk $W/pilot; mk $W/ps
PH1=$(sed -n 2p $IN/r1col.r1spec | cut -d, -f1); PH2=$(sed -n 2p $IN/r1col-comp.csv | cut -d, -f1); PILOT=$PH1,$PH2
R1KEEP=$PILOT python3 $W/pilot.py jan > pilot/run.log 2>&1; rc=$?
[ $rc = 0 ] && [ ! -e pilot/fail-jan.txt ] && [ -e pilot/psim-jan.done ] || { echo "пилот красный rc=$rc" > rc.txt; exit 5; }
st "пилот 2 хеша × янв"
mkdir -p ps/b; ln -sfn /data/p12r2/v-b/study ps/b/study
one() { mo=$1; T1=$(date +%s)
  R1KEEP=$(ls $W/cells/$mo | sed 's/\.csv$//' | paste -sd,) python3 $W/ps.py $mo > ps/run-$mo.log 2>&1; r=$?
  echo "full $mo rc=$r s=$(( $(date +%s)-T1 )) клеток=$(ls $W/cells/$mo | wc -l)" >> timing.txt; }
export -f one; export W
echo $MONS | tr ' ' '\n' | xargs -P "$P" -I{} bash -c 'one {}'
st "psim 9 мес."
bad=0; for m in $MONS; do [ -e ps/psim-$m.done ] && [ ! -e ps/fail-$m.txt ] || { echo "красный $m" >> timing.txt; bad=1; }; done
python3 - >> timing.txt <<PY
import json
bad = 0
for cap in (0, 3):
    a = json.load(open("$W/pilot/closes-cap%d-jan.json" % cap)); b = json.load(open("$W/ps/closes-cap%d-jan.json" % cap))
    for h in "$PILOT".split(","):
        ok = a.get(h) == b.get(h); bad += not ok
        print("пилот==полный", "cap%d" % cap, h, "OK" if ok else "DIFF", "(нет сделок)" if a.get(h) is None else "")
print("расхождений пилот/полный", bad)
PY
python3 $IN/sum_closes_r1.py $W/ps $W/closes-r1col-sum.csv > $W/sum-tot.txt 2>&1 || bad=1
st "сводка closes"
echo "bad=$bad" > rc.txt; exit $bad
