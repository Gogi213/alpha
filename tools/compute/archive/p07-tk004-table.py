#!/usr/bin/env python3
"""TK-004: таблица ступени 2 Г-85б (+ H6 at/behind Г-85а) и правило продолжения А/Б/В
(`docs/research/reviews/P-07-2026-09-27-continuation-rule.md`). Читает ТОЛЬКО клетки ступени 2 — ключи ниже;
ступень 3 (H1, H3, H4, H5, H8, H13) запечатана до решения А/Б/В и здесь не открывается.

    python tools/compute/p07-tk004-table.py > docs/findings/p07-tk004-stage2-table.md
"""
import json
import sys

B_STAGE2 = (["base", "H2-0.0136", "H2-0.0273", "H2-0.0682", "H6-pct1.5", "H6-pct3", "H6-at", "H6-behind",
             "H7-tr1.5x1", "H7-tr1x1.5", "H7-tr2x1", "H7-1to1", "H10-cap3", "H10-cap5",
             "H9r-s1", "H9r-s2", "H9r-s3", "H14-k1", "H14-k2", "H14-k3", "H14-n2", "H14-n3", "H14-n4", "keep-keep"])
A_STAGE2 = ["base", "H6-at", "H6-behind"]


def f(x):
    return "—" if x is None else f"{x:.3f}".replace(".", ",")


def d(x):
    if not x:
        return "—"
    lo, hi = x["ci95"]
    return f"{x['est']:+.0f} [{lo:+.0f};{hi:+.0f}]"


def table(data, keys, base_key="base"):
    base = data[base_key]["kpi"]["frac_gt_5d"]
    out = ["| клетка | авг: доля (сут/мон) | сен: доля (сут/мон) | n авг/сен | авг $ | сен $ | Δ$ авг [95%] | Δ$ сен [95%] "
           "| §10 | Б: 1 / 2 / 3 |", "|---|---|---|---|---|---|---|---|---|---|"]
    res = {}
    for k in keys:
        r = data[k]
        kp = r["kpi"] or {}
        fr, dm, sm = kp.get("frac_gt_5d", {}), kp.get("stability_day_max", {}), kp.get("stability_symbol_max", {})
        dv = r.get("diff_vs_base") or {}
        c1 = c2 = c3 = None
        if k != base_key and fr:
            c1 = fr["aug"] <= base["aug"] - 0.10 and fr["sep"] <= base["sep"]
            c2 = (dm.get("aug") is not None and dm["aug"] <= base["aug"] - 0.10
                  and dm.get("sep") is not None and dm["sep"] <= base["sep"])
            c3 = all(dv.get(m) and dv[m]["ci95"][1] >= 0 for m in ("aug", "sep"))
        yn = lambda b: "—" if b is None else ("да" if b else "нет")
        out.append(f"| {k} | {f(fr.get('aug'))} ({f(dm.get('aug'))}/{f(sm.get('aug'))}) | "
                   f"{f(fr.get('sep'))} ({f(dm.get('sep'))}/{f(sm.get('sep'))}) | {r['n']['aug']}/{r['n']['sep']} | "
                   f"{r['usd']['aug']:.0f} | {r['usd']['sep']:.0f} | {d(dv.get('aug'))} | {d(dv.get('sep'))} | "
                   f"{kp.get('verdict', '—')} | {yn(c1)} / {yn(c2)} / {yn(c3)} |")
        res[k] = {"frac": fr, "day_max": dm, "sym_max": sm, "usd": r["usd"], "n": r["n"],
                  "diff": dv, "verdict": kp.get("verdict"), "B": [c1, c2, c3]}
    return "\n".join(out), res


def main():
    b = json.load(open("data/p07/tk004-read-b2.json", encoding="utf-8"))
    a3 = json.load(open("data/p07/tk004-read-a3.json", encoding="utf-8"))
    tb, rb = table(b, B_STAGE2)
    ta, ra = table({k: a3[k] for k in A_STAGE2}, A_STAGE2)
    print("## Г-85б — ступень 2\n\n" + tb + "\n\n## Г-85а — H6 at/behind (замена вырожденной before)\n\n" + ta)
    json.dump({"g85b": rb, "g85a_h6": ra}, open("docs/findings/p07-tk004-stage2-2026-09-28.json", "w",
              encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    sys.exit(main())
