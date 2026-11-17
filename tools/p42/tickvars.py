"""П-42 §13.1/§13.2: топ-3 шага A по стороне (правило записано до чтения шага A) и файл вариантов шага B.

  P42_ROOT=data/p42v2 python tools/p42/tickvars.py   → data/p42v2/variants-tick.txt, data/p42v2/top3.json
Топ-3: среди вариантов шага 1, расширений и совместной сетки (без базы, кандидатов, справочных slip/best/u*) —
наибольший min(среднее $/сут. A, среднее $/сут. B) при ≥ 10 сделках в каждой половине; ничья — больше сделок всего;
варианты с побайтово теми же сделками, что у базы или уже взятого, пропускаются.
Шаг B считает: базу, кандидатов (cA_/cB_ и «по букве» uA_/uB_), топ-3; справочно — задержку {250, 1000} мс и проскальзывание {0, 2, 5} bps
для базы и кандидатов (не испытания).
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from lib import HALVES, ROOT, SIDES, load_all  # noqa: E402


def read_lines():
    d = {}
    for f in os.listdir(ROOT):
        if f.startswith("variants-") and f.endswith(".txt") and f not in ("variants-tick.txt",):
            for ln in open(os.path.join(ROOT, f), encoding="utf-8"):
                ln = ln.strip()
                if ln:
                    d[ln.split()[0]] = ln
    return d


def trades_hash(vdir, side):
    return hashlib.md5(open(os.path.join(vdir, side + "-trades.csv"), "rb").read()).hexdigest()


def main():
    D, P, T = load_all()
    lines = read_lines()
    top = {}
    for side in SIDES:
        base_h = trades_hash(P[(side, "b0")], side)
        seen = {base_h}
        rows = []
        for v in D[side]:
            if not (v.startswith("v_") or v.startswith("j_")):
                continue
            nA, nB = T[side][v]
            if nA < 10 or nB < 10:
                continue
            mA, mB = float(D[side][v][HALVES["A"]].mean()), float(D[side][v][HALVES["B"]].mean())
            rows.append((min(mA, mB), nA + nB, v, mA, mB, nA, nB))
        rows.sort(key=lambda r: (-r[0], -r[1], r[2]))
        pick = []
        for r in rows:
            h = trades_hash(P[(side, r[2])], side)
            if h in seen:
                continue
            seen.add(h)
            pick.append(r)
            if len(pick) == 3:
                break
        top[side] = [{"vid": r[2], "min_half": r[0], "A": r[3], "B": r[4], "nA": r[5], "nB": r[6]} for r in pick]
        top[side + "_eligible"] = len(rows)
    json.dump(top, open(os.path.join(ROOT, "top3.json"), "w"), indent=1)
    out = []

    def add(i, base_id):
        out.append((i, base_id))

    ids = ["b0"]
    for side in SIDES:
        ids += ["cA_" + side, "cB_" + side, "uA_" + side, "uB_" + side] + [t["vid"] for t in top[side]]
    seen_ids = []
    for i in ids:
        if i in lines and i not in seen_ids:
            seen_ids.append(i)
    # b0 может не быть в lines (база без переопределений): строка «b0»
    res = []
    for i in seen_ids:
        res.append(lines[i])
    if "b0" not in lines:
        res.insert(0, "b0")
    ref = []
    for i in ["b0"] + [x for x in seen_ids if x[:3] in ("cA_", "cB_")]:
        pre = lines.get(i, i)
        for k, v in (("lat", 250), ("lat", 1000), ("slip", 0), ("slip", 2), ("slip", 5)):
            ref.append("%s_%s%d %s" % (i, k, v, " ".join(pre.split()[1:] + ["%s=%d" % (k, v)])))
    with open(os.path.join(ROOT, "variants-tick.txt"), "w", encoding="utf-8") as f:
        for ln in res + ref:
            f.write(ln.strip() + "\n")
    print(json.dumps(top, ensure_ascii=False, indent=1))
    print("variants-tick:", len(res), "+", len(ref), "справочных")


if __name__ == "__main__":
    main()
