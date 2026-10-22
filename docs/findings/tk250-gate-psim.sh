#!/bin/bash
# TK-250 гейт П1 п.2: psim по 6 клеткам r1cell.py (фев) -> closes-cap0/cap3-feb.json; сделки по хешу == канон /data/tk063r1 (имена g39-k20…). Пишет в /data/tk0250/ps, канон только читает.
W=/data/tk0250; cd $W || exit 9
python3 - <<'PY' || exit 3
s = open("/data/tk063r1/p12-r1-psim.py", encoding="utf-8").read()
s = s.replace('O = "/data/tk063r1"', 'O = "/data/tk0250/ps"').replace('CELLS = "/data/tk064/r1/cells"', 'CELLS = "/data/tk0250/out/cells"')
a = "        cell = os.path.basename(cf)[:-4]\n"
assert a in s
s = s.replace(a, a + "        if cell not in os.environ['R1KEEP'].split(','):\n            continue\n")
open("/data/tk0250/psim.py", "w", encoding="utf-8").write(s)
PY
T0=$(date +%s)
R1KEEP=$(cut -d' ' -f1 map-r1ext.txt | paste -sd,) python3 psim.py feb > psim.log 2>&1; echo "psim rc=$? s=$(( $(date +%s)-T0 ))" > gate2.txt
ls ps/fail-feb.txt >> gate2.txt 2>&1
python3 - >> gate2.txt <<'PY'
import json
names = dict(l.split() for l in open("/data/tk0250/map-r1ext.txt"))
bad = 0
for cap in (0, 3):
    new = json.load(open(f"/data/tk0250/ps/closes-cap{cap}-feb.json")); old = json.load(open(f"/data/tk063r1/closes-cap{cap}-feb.json"))
    for h, n in names.items():
        a, b = new.get(h), old.get(n)
        ok = a == b and a is not None
        tot = lambda v: sum(len(l) for cc in (v or {}).values() for l in cc.values())
        print(("OK" if ok else "DIFF"), f"cap{cap}", h, n, "сделок", tot(a), tot(b))
        bad += not ok
print("расхождений", bad)
PY
