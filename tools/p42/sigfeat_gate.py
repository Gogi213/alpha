"""П-42 §13.17 ворота (1)–(3): v19 против v18 побайтово; строки sigsx = строки sigs; сигналы ↔ уровни (levels.csv).
Использование: python3 sigfeat_gate.py <out/eng> — печатает итог, код возврата 1 при расхождении."""
import hashlib
import json
import os
import sys

import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sigfeat_lib import read_levels_csv, read_sigsx

ENG = sys.argv[1]
E16 = "/data/p42-t566/out/e16"
OLD = {"b0": f"{E16}/b0", "b0pk": "/data/p42-t566/out/ow/b0pk", "ow": f"{E16}/ow"}
KINDS = ("trades.csv", "daily.csv", "coinday.csv", "detail.jsonl")
bad = []
rep = {"bytes": {}, "rows": {}, "levels": {}}


def md5(p):
    return hashlib.md5(open(p, "rb").read()).hexdigest()


for v, old in OLD.items():
    for side in ("long", "short"):
        for k in KINDS:
            a, b = f"{ENG}/{v}/{side}-{k}", f"{old}/{side}-{k}"
            if not os.path.exists(b):
                rep["bytes"][f"{v}/{side}-{k}"] = "нет эталона"
                continue
            same = md5(a) == md5(b)
            rep["bytes"][f"{v}/{side}-{k}"] = same
            if not same:
                bad.append(f"{v}/{side}-{k}")
for v in ("b0", "b0p0", "b0pk", "ow"):
    for side in ("long", "short"):
        n1 = sum(1 for _ in open(f"{ENG}/{v}/{side}-sigsx.csv", encoding="utf-8")) - 1
        n2 = sum(1 for _ in open(f"{ENG}/{v}/{side}-sigs.csv", encoding="utf-8")) - 1
        rep["rows"][f"{v}/{side}"] = [n1, n2]
        if n1 != n2:
            bad.append(f"rows {v}/{side}")
# сигналы ↔ уровни
for v in ("b0", "b0p0"):
    for side in ("long", "short"):
        sg = read_sigsx(f"{ENG}/{v}/{side}-sigsx.csv")
        lv = read_levels_csv(f"{ENG}/{v}/{side}-levels.csv")
        key = lv.set_index(["sym", "level"])
        miss = born_bad = end_bad = fb_bad = price_bad = mean_bad = 0
        for r in sg.itertuples(index=False):
            try:
                row = key.loc[(r.sym, r.level)]
            except KeyError:
                miss += 1
                continue
            if row.born != r.born:
                born_bad += 1
            if row.end != r.end:
                end_bad += 1
            if row.first_bar != r.first_bar:
                fb_bad += 1
            t = r.sig_ms // 60000
            segs = [tuple(map(int, x.split(":"))) for x in row.segs.split("|")]
            si = max(i for i, (m, _) in enumerate(segs) if m <= t)
            if segs[si][1] != r.level_px_e8:
                price_bad += 1
            mem = row.members.split("|")[si]
            mem_sg = r.members
            if mem != mem_sg.replace("|", ","):
                mean_bad += 1
            px = [int(x.split(":")[1]) for x in mem_sg.split("|")]
            if abs(sum(px) // len(px) - r.level_px_e8) > len(px):
                mean_bad += 1
        rep["levels"][f"{v}/{side}"] = {"signals": int(len(sg)), "levels": int(len(lv)), "missing": miss, "born_bad": born_bad, "end_bad": end_bad, "first_bar_bad": fb_bad,
                                         "price_bad": price_bad, "members_bad": mean_bad}
        if any((miss, born_bad, end_bad, fb_bad, price_bad, mean_bad)):
            bad.append(f"levels {v}/{side}")
rep["bad"] = bad
print(json.dumps(rep, ensure_ascii=False, indent=1))
sys.exit(1 if bad else 0)
