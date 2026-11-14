"""П-42 замечание проверки 14.11, п. 4/5: слова вердиктов до и после (итоговое m + детерминированный бутстреп) → <ROOT>/m-check.json и печать.

  python tools/p42/mcheck.py <каталог старых отчётов> <итоговое m JSON>
Сравниваются: report-excl22.json (вселенные full и excl22), report-walkfwd.json (чтение клетки), report-544.json (full и re544).
"""
import json
import os
import sys

OLD = sys.argv[1]
M = json.loads(sys.argv[2])
ROOT = os.environ.get("P42_ROOT", "data/p42v2")
NEW = {"report-excl22.json": os.path.join(ROOT, "report-excl22.json"),
       "report-walkfwd.json": os.path.join(ROOT, "..", "p42v2-wf", "report-walkfwd.json"),
       "report-544.json": os.path.join(ROOT, "..", "p42v2-544", "report-544.json")}


def words(name, R):
    out = {}
    for key, row in R["res"].items():
        if name == "report-walkfwd.json":
            out[key] = row["reading"]
        else:
            for u, c in row.items():
                if isinstance(c, dict) and "verdict" in c:
                    out["%s|%s" % (key, u)] = c["verdict"]
    return out


changed, n = [], 0
for name, new_p in NEW.items():
    a = words(name, json.load(open(os.path.join(OLD, name), encoding="utf-8")))
    b = words(name, json.load(open(new_p, encoding="utf-8")))
    assert a.keys() == b.keys(), (name, set(a) ^ set(b))
    for k in a:
        n += 1
        if a[k] != b[k]:
            changed.append("%s %s: «%s» → «%s»" % (name, k, a[k], b[k]))
res = {"m": M, "compared": n, "changed": changed}
json.dump(res, open(os.path.join(ROOT, "m-check.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
print("compared", n, "changed", len(changed))
for c in changed:
    print(" ", c)
