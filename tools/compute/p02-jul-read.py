#!/usr/bin/env python3
"""TK-018 (В-151): блок A П-02 на июле 2026 — ОПИСАНИЕ, не проверка (у П-02 правила чтения июля нет; Холм не
применяется). Счёт — `p02-wall.py scan --july` (пороги Г-28 заморожены по августу, как в П-02), чтение — функцией
`read` из `p02-r2-read.py` (объединённая разность долей `bounced`, круговой блочный бутстреп по суткам, b = ⌈n^⅓⌉,
20 000 повторов, seed 20260926; сутки с наибольшим влиянием; знаковый тест). Сверка до июля: август и сентябрь из
того же файла счётов = `data/p02/p02-wall-counts-2026-09-26.json` по каждым суткам (иначе стоп, без чисел июля).
Страница: `p02.rows[h].months["июль"]` (est/lo/hi/days/days_plus, `p_holm = None`, `desc = true`).

    python tools/compute/p02-jul-read.py --counts data/p02/p02-wall-counts-jul-2026-09-28.json \\
        --out docs/findings/p02-jul-2026-09-28.json \\
        [--data data/titration-dashboard/data-v40pre2.json --data-out data/titration-dashboard/data-v40pre3.json]
"""
import argparse
import importlib.util
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("r2", os.path.join(HERE, "p02-r2-read.py"))
R2 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(R2)
JUL = ("2026-07", 31)
HYP = {h: v for h, v in R2.BLOCK_A.items() if v[0] == "p02-wall-counts-2026-09-26.json"}  # Г-28, Г-08


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--counts", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--data")
    ap.add_argument("--data-out")
    ap.add_argument("--reps", type=int, default=20000)
    ap.add_argument("--seed", type=int, default=20260926)
    a = ap.parse_args()
    new = json.load(open(a.counts, encoding="utf-8"))
    old = json.load(open(os.path.join(R2.DATA, "p02-wall-counts-2026-09-26.json"), encoding="utf-8"))
    assert new["thresholds"] == old["thresholds"], "пороги августа не совпали"
    dc, dco = new["day_counts"], old["day_counts"]
    bad = [d for d in dco if dc.get(d) != dco[d]]
    assert not bad, f"авг/сен не совпали с прежним счётом: {bad[:5]}"
    res = {"_meta": {"task": "TK-018", "kind": "описание, не проверка (июль вне П-02)", "counts": a.counts,
                     "gate_augsep_days_equal": len(dco)}}
    for h, (_f, var, up, base) in HYP.items():
        days = sorted(d for d in dc if d.startswith(JUL[0]) and var in dc[d].get("variants", dc[d]))
        units, keep = [], []
        for d in days:
            v = dc[d].get("variants", dc[d])[var]
            u = (v[up][0], v[up][1], v[base][0], v[base][1])
            if u[1] > 0 or u[3] > 0:
                units.append(u)
                keep.append(d)
        eff = lambda u: ((u[0] / u[1] if u[1] else 0) - (u[2] / u[3] if u[3] else 0)) * 100 if u[1] and u[3] else 0.0
        res[h] = R2.read(units, keep, R2.stat_a, eff, a.reps, a.seed)
    with open(a.out, "w", encoding="utf-8", newline="") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(a.out)
    if a.data:
        D = json.load(open(a.data, encoding="utf-8"))
        n = 0
        for row in D["p02"]["rows"]:
            x = res.get(row["h"])
            if x is None:
                continue
            st = x.get("sign_test") or {}
            row["months"]["июль"] = {"est": round(x["est"], 2), "lo": round(x["ci95"][0], 2), "hi": round(x["ci95"][1], 2),
                                     "p_holm": None, "days_plus": st.get("days_plus"), "days": st.get("days"),
                                     "drop_ok": x["sign_kept_after_drops"], "desc": True}
            n += 1
        with open(a.data_out, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(D, ensure_ascii=False, separators=(",", ":")))
        print(f"июль в строках П-02: {n}", a.data_out)


if __name__ == "__main__":
    main()
