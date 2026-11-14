"""П-42 §13.10: честный эталон «перекрёстный вне выборки» — оси принимаются и значения выбираются ТОЛЬКО на половине подбора.

  P42_ROOT=data/p42v2-t544 python tools/p42/honest.py      → <ROOT>/variants-honest.txt (hA_<сторона>, hB_<сторона>) и <ROOT>/cfg-honest.json
Для половины h: значения — выбранные на h (phase.analyze_axes, half[h].sel); ось принимается, если выбор ≠ база, Δ_h > 0 и плато на h
(у двоичных осей плато не требуется); клетка совместной сетки половины h (phase.joint_select) принимается при Δ_h > 0; t_low < t_high как в decide.
hA_ (выбран на A) считается на сутках B, hB_ — на сутках A. Следом: `python tools/p42/tgrid.py decorate honest`, `bash tools/p42/runtgrid.sh <U> honest`.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from axes import BASE, line  # noqa: E402
from lib import ROOT, SIDES, load_all  # noqa: E402
from phase import analyze_axes, joint_select, rng  # noqa: E402


def honest_cfg(D, T, side, r, plan):
    out, _ = analyze_axes(D, side, r, T)
    cfg = {"A": {}, "B": {}}
    log = {"A": {}, "B": {}}
    for h in ("A", "B"):
        for ax, x in out.items():
            hh = x["half"][h]
            i = hh["sel"]
            if i == x["base_i"]:
                continue
            ok = hh["delta"] > 0 and (x["binary"] or hh["plateau"] is True)
            log[h][ax] = {"value": x["vals"][i], "delta": hh["delta"], "plateau": hh["plateau"], "accepted": bool(ok)}
            if ok:
                cfg[h][ax] = x["vals"][i]
        j = joint_select(D, side, plan[side][h], h, r, T)
        if j and j["delta_sel"] > 0:
            for ax, v in zip(plan[side][h]["pair"], j["cell"]):
                cfg[h][ax] = v
            log[h]["joint"] = {"pair": plan[side][h]["pair"], "cell": j["cell"], "delta": j["delta_sel"]}
        tl, th = cfg[h].get("t_low", BASE["t_low"]), cfg[h].get("t_high", BASE["t_high"])
        if tl >= th:
            dl = abs(out["t_low"]["half"][h]["delta"]) if "t_low" in out else 0
            dh = abs(out["t_high"]["half"][h]["delta"]) if "t_high" in out else 0
            drop = "t_high" if dh <= dl else "t_low"
            cfg[h].pop(drop, None)
            log[h]["dropped"] = drop
    return cfg, log


def main():
    D, _, T = load_all()
    r = rng()
    plan = json.load(open(os.path.join(ROOT, "plan-joint.json")))
    res, lines = {}, []
    for side in SIDES:
        cfg, log = honest_cfg(D, T, side, r, plan)
        res[side] = {"cfg": {h: {k: v for k, v in cfg[h].items() if BASE[k] != v} for h in ("A", "B")}, "log": log}
        for h in ("A", "B"):
            p = res[side]["cfg"][h]
            if p:
                lines.append(line("h%s_%s" % (h, side), p))
            print(side, "h%s (выбран на %s, считается на %s):" % (h, h, "B" if h == "A" else "A"), p or "ничего не принято → база")
    with open(os.path.join(ROOT, "variants-honest.txt"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(lines) + "\n")
    json.dump(res, open(os.path.join(ROOT, "cfg-honest.json"), "w", encoding="utf-8"), indent=1, ensure_ascii=False,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))
    print("variants-honest:", len(lines))


if __name__ == "__main__":
    main()
