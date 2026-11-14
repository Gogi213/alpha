"""П-42 §9: перекрёстный выбор по осям, расширение на краю, вторая итерация (две оси), сборка кандидата.

  python tools/p42/phase.py axes            # таблица осей по сторонам (выбор A→B, B→A) + список расширений
  python tools/p42/phase.py ext             # → data/p42/variants-ext.txt (расширения на краю) и plan-ext.json
  python tools/p42/phase.py joint           # → data/p42/variants-joint.txt и plan-joint.json
  python tools/p42/phase.py final           # → data/p42/variants-final.txt (кандидаты + справочные) и cfg-final.json
"""
import itertools
import json
import os
import sys

import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from axes import AXES, BASE, VACUOUS, fmt, line, vid  # noqa: E402
from lib import (A, B, EXT, HALVES, ROOT, SIDES, axis_vals, load_all, pick, smooth, stat, vid_of)  # noqa: E402

OTHER = {"A": "B", "B": "A"}


def rng():
    return np.random.default_rng(42)


def analyze_axes(D, side, r, T, mask=True):
    """Выбор значения по каждой оси на A и на B (по сглаженному среднему), проверка на другой половине."""
    base = D[side]["b0"]
    out = {}
    tests = []
    for ax in AXES:
        if ax in VACUOUS:
            continue
        vals = axis_vals(ax, D[side])
        vids = [vid_of(ax, v) for v in vals]
        if any(x not in D[side] for x in vids):
            continue
        ser = [D[side][x] for x in vids]
        bi = vals.index(BASE[ax])
        res = {"vals": vals, "base_i": bi, "binary": len(vals) == 2, "half": {}, "val": {}}
        for h, sl in HALVES.items():
            m = [float(s[sl].mean()) for s in ser]
            sm = m if len(vals) == 2 else smooth(m)
            ntr = [T[side][x][0 if h == "A" else 1] for x in vids]
            i = pick(sm, bi, [n > 0 for n in ntr] if mask else None)
            dsel = ser[i][sl] - base[sl]
            res["half"][h] = {"m": m, "sm": sm, "sel": i, "delta": float(dsel.mean()), "ntr": ntr}
            if i != bi:
                nb = [j for j in (i - 1, i + 1) if 0 <= j < len(vals)]
                se = stat(dsel, r)["se"]
                dn = {j: float((ser[j][sl] - base[sl]).mean()) for j in nb}
                res["half"][h]["plateau"] = bool(all(v > 0 and v >= float(dsel.mean()) - se for v in dn.values()))
                res["half"][h]["nb_delta"] = {str(j): v for j, v in dn.items()}
            else:
                res["half"][h]["plateau"] = None
        for h in ("A", "B"):
            i = res["half"][h]["sel"]
            d = ser[i][HALVES[OTHER[h]]] - base[HALVES[OTHER[h]]]
            st = stat(d, r)
            res["val"][h + "->" + OTHER[h]] = {"idx": i, "value": vals[i], **st}
            tests.append({"side": side, "kind": "axis", "ax": ax, "dir": h + "->" + OTHER[h], "p": st["p"]})
        ia, ib = res["half"]["A"]["sel"], res["half"]["B"]["sel"]
        res["accepted"] = bool(res["val"]["A->B"]["mean"] > 0 and res["val"]["B->A"]["mean"] > 0 and abs(ia - ib) <= 1)
        # расширение: выбор на краю (не база, не двоичная ось)
        need = []
        if len(vals) > 2:
            lo, hi = EXT.get(ax, (None, None))
            for h in ("A", "B"):
                i = res["half"][h]["sel"]
                if i == bi:
                    continue
                if i == 0 and lo is not None and vid(ax, lo) not in D[side]:
                    need.append(lo)
                if i == len(vals) - 1 and hi is not None and vid(ax, hi) not in D[side]:
                    need.append(hi)
        res["need_ext"] = sorted(set(need))
        out[ax] = res
    return out, tests


def cmd_axes():
    D, _, T = load_all()
    r = rng()
    for side in SIDES:
        out, _ = analyze_axes(D, side, r, T)
        print("== %s: оси" % side)
        for ax, x in out.items():
            ha, hb = x["half"]["A"], x["half"]["B"]
            print("%-12s vals=%s  selA=%s (Δ_A %+.2f)  selB=%s (Δ_B %+.2f)  A→B %+.2f  B→A %+.2f  accepted=%s plateau=%s/%s ext=%s" % (
                ax, [fmt(v) for v in x["vals"]], fmt(x["vals"][ha["sel"]]), ha["delta"], fmt(x["vals"][hb["sel"]]), hb["delta"],
                x["val"]["A->B"]["mean"], x["val"]["B->A"]["mean"], x["accepted"], ha["plateau"], hb["plateau"], x["need_ext"]))


def cmd_ext():
    D, _, T = load_all()
    r = rng()
    plan = {}
    allv = {}
    for side in SIDES:
        out, _ = analyze_axes(D, side, r, T)
        plan[side] = []
        for ax, x in out.items():
            for v in x["need_ext"]:
                plan[side].append({"ax": ax, "value": v, "vid": vid(ax, v)})
                allv[vid(ax, v)] = {ax: v}
    json.dump(plan, open(os.path.join(ROOT, "plan-ext.json"), "w"), indent=1)
    with open(os.path.join(ROOT, "variants-ext.txt"), "w", encoding="utf-8") as f:
        for k, p in allv.items():
            f.write(line(k, p) + "\n")
    print("расширений:", {s: len(plan[s]) for s in SIDES}, "уникальных вариантов:", len(allv))


def joint_axes(out, h):
    """Две оси с наибольшим |эффектом| на половине подбора (выбор не база)."""
    cand = [(abs(x["half"][h]["delta"]), ax) for ax, x in out.items() if x["half"][h]["sel"] != x["base_i"]]
    cand.sort(reverse=True)
    return sorted(ax for _, ax in cand[:2])


def joint_id(a1, v1, a2, v2):
    ids = []
    for ax, v in ((a1, v1), (a2, v2)):
        ids.append("%s_%s" % (ax, fmt(v).replace(".", "p")))
    return "j_" + "__".join(ids)


def joint_cell_vid(a1, v1, a2, v2):
    n_non_base = (v1 != BASE[a1]) + (v2 != BASE[a2])
    if n_non_base == 0:
        return "b0", {}
    if n_non_base == 1:
        ax, v = (a1, v1) if v1 != BASE[a1] else (a2, v2)
        return vid(ax, v), {ax: v}
    return joint_id(a1, v1, a2, v2), {a1: v1, a2: v2}


def joint_grid(out, h, pair):
    """Значения по двум осям: выбранное и его соседи по оси (≤ 3 на ось)."""
    g = []
    for ax in pair:
        x = out[ax]
        i = x["half"][h]["sel"]
        idxs = [j for j in (i - 1, i, i + 1) if 0 <= j < len(x["vals"])]
        g.append([x["vals"][j] for j in idxs])
    return g


def cmd_joint():
    D, _, T = load_all()
    r = rng()
    plan = {}
    allv = {}
    for side in SIDES:
        out, _ = analyze_axes(D, side, r, T)
        plan[side] = {}
        new = set()
        for h in ("A", "B"):
            pair = joint_axes(out, h)
            if len(pair) < 2:
                plan[side][h] = {"pair": pair, "cells": []}
                continue
            g = joint_grid(out, h, pair)
            cells = []
            for v1, v2 in itertools.product(*g):
                k, p = joint_cell_vid(pair[0], v1, pair[1], v2)
                cells.append({"v": [v1, v2], "vid": k})
                if k not in D[side] and k not in allv:
                    allv[k] = p
                if k.startswith("j_"):
                    new.add(k)
            plan[side][h] = {"pair": pair, "cells": cells}
        plan[side]["new_trials"] = sorted(new)
    json.dump(plan, open(os.path.join(ROOT, "plan-joint.json"), "w"), indent=1)
    with open(os.path.join(ROOT, "variants-joint.txt"), "w", encoding="utf-8") as f:
        for k, p in allv.items():
            f.write(line(k, p) + "\n")
    print("совместные клетки:", {s: len(plan[s]["new_trials"]) for s in SIDES}, "вариантов к счёту:", len(allv))
    for s in SIDES:
        print(s, {h: plan[s][h]["pair"] for h in ("A", "B")})


def joint_select(D, side, plan_h, h, r, T):
    """Выбор клетки на половине h (сглаживание: клетка + соседи по осям), проверка на другой половине."""
    pair = plan_h["pair"]
    cells = plan_h["cells"]
    if len(pair) < 2 or any(c["vid"] not in D[side] for c in cells):
        return None
    vals = {}
    for c in cells:
        vals.setdefault(0, set()).add(c["v"][0])
        vals.setdefault(1, set()).add(c["v"][1])
    v1s, v2s = sorted(vals[0]), sorted(vals[1])
    base = D[side]["b0"]
    sl, ot = HALVES[h], HALVES[OTHER[h]]
    M = {}
    for c in cells:
        M[(c["v"][0], c["v"][1])] = (float(D[side][c["vid"]][sl].mean()), c["vid"])
    sm = {}
    for (a, b), (m, _) in M.items():
        i, j = v1s.index(a), v2s.index(b)
        nb = [M[(v1s[ii], v2s[jj])][0] for ii, jj in ((i, j), (i - 1, j), (i + 1, j), (i, j - 1), (i, j + 1))
              if 0 <= ii < len(v1s) and 0 <= jj < len(v2s)]
        sm[(a, b)] = float(np.mean(nb))
    ok = {k: T[side][M[k][1]][0 if h == "A" else 1] > 0 for k in sm}
    allowed = [k for k in sm if ok[k]]
    if not allowed:
        return None
    best = max(sm[k] for k in allowed)
    cand = [k for k in allowed if abs(sm[k] - best) < 1e-12]
    bk = min(cand, key=lambda k: abs(v1s.index(k[0]) - (v1s.index(BASE[pair[0]]) if BASE[pair[0]] in v1s else 99)) +
             abs(v2s.index(k[1]) - (v2s.index(BASE[pair[1]]) if BASE[pair[1]] in v2s else 99)))
    d = D[side][M[bk][1]][ot] - base[ot]
    st = stat(d, r)
    return {"cell": list(bk), "vid": M[bk][1], "idx": (v1s.index(bk[0]), v2s.index(bk[1])), "val": st,
            "delta_sel": float((D[side][M[bk][1]][sl] - base[sl]).mean())}


def decide(D, side, r, T, mask=True, use_joint=True):
    """Принятые значения по осям и результат второй итерации → cfg для A→B (cfgA) и B→A (cfgB)."""
    out, tests = analyze_axes(D, side, r, T, mask)
    cfg = {"A": {}, "B": {}}
    for h in ("A", "B"):
        for ax, x in out.items():
            if x["accepted"]:
                cfg[h][ax] = x["vals"][x["half"][h]["sel"]]
    joint = None
    pj = os.path.join(ROOT, "plan-joint.json")
    if use_joint and os.path.exists(pj):
        plan = json.load(open(pj))[side]
        ja = joint_select(D, side, plan["A"], "A", r, T)
        jb = joint_select(D, side, plan["B"], "B", r, T)
        if ja and jb:
            tests.append({"side": side, "kind": "joint", "ax": "+".join(plan["A"]["pair"]), "dir": "A->B", "p": ja["val"]["p"]})
            tests.append({"side": side, "kind": "joint", "ax": "+".join(plan["B"]["pair"]), "dir": "B->A", "p": jb["val"]["p"]})
            same = plan["A"]["pair"] == plan["B"]["pair"]
            adj = max(abs(ja["idx"][0] - jb["idx"][0]), abs(ja["idx"][1] - jb["idx"][1])) <= 1 if same else False
            acc = bool(same and adj and ja["val"]["mean"] > 0 and jb["val"]["mean"] > 0)
            joint = {"A": ja, "B": jb, "same_pair": same, "adjacent": adj, "accepted": acc, "pair": plan["A"]["pair"]}
            if acc:
                for h, jx in (("A", ja), ("B", jb)):
                    for ax, v in zip(plan[h]["pair"], jx["cell"]):
                        cfg[h][ax] = v
    # лестница тейков должна идти вверх: при t_low ≥ t_high возвращается к базе ось с меньшим эффектом на половине подбора
    for h in ("A", "B"):
        tl, th = cfg[h].get("t_low", BASE["t_low"]), cfg[h].get("t_high", BASE["t_high"])
        if tl >= th:
            dl = abs(out["t_low"]["half"][h]["delta"]) if "t_low" in out else 0
            dh = abs(out["t_high"]["half"][h]["delta"]) if "t_high" in out else 0
            drop = "t_high" if dh <= dl else "t_low"
            cfg[h].pop(drop, None)
            cfg.setdefault("dropped", {})[h] = drop
    return out, cfg, joint, tests


def cmd_final():
    D, _, T = load_all()
    r = rng()
    cfgs = {}
    allv = {}
    for side in SIDES:
        out, cfg, joint, tests = decide(D, side, r, T)
        cfgs[side] = {"cfg": {h: {k: v for k, v in cfg[h].items()} for h in ("A", "B")}, "dropped": cfg.get("dropped", {}),
                      "joint": None if joint is None else {k: v for k, v in joint.items() if k not in ("A", "B")} | {
                          "A": joint["A"]["cell"], "B": joint["B"]["cell"]}}
        for h in ("A", "B"):
            p = {k: v for k, v in cfg[h].items() if BASE[k] != v}
            if not p:
                continue
            cid = "c%s_%s" % (h, side)
            allv[cid] = p
            for sl in (0, 2, 5):
                allv["%s_slip%d" % (cid, sl)] = dict(p, slip=sl)
            allv["%s_best" % cid] = dict(p, best=1)
    for side in SIDES:   # «по букве протокола»: без маски нулевых сделок и без второй итерации — справочный кандидат
        _, cfg_l, _, _ = decide(D, side, r, T, mask=False, use_joint=False)
        cfgs[side]["letter"] = {h: cfg_l[h] for h in ("A", "B")}
        for h in ("A", "B"):
            p = {k: v for k, v in cfg_l[h].items() if BASE[k] != v}
            if p:
                allv["u%s_%s" % (h, side)] = p
    for sl in (0, 2, 5):
        allv["b0_slip%d" % sl] = {"slip": sl}
    allv["b0_best"] = {"best": 1}
    json.dump(cfgs, open(os.path.join(ROOT, "cfg-final.json"), "w"), indent=1)
    with open(os.path.join(ROOT, "variants-final.txt"), "w", encoding="utf-8") as f:
        for k, p in allv.items():
            f.write(line(k, p) + "\n")
    for s in SIDES:
        print(s, "cfgA(A→B):", cfgs[s]["cfg"]["A"], "cfgB(B→A):", cfgs[s]["cfg"]["B"], "joint:", cfgs[s]["joint"])
    print("вариантов в final:", len(allv))


if __name__ == "__main__":
    {"axes": cmd_axes, "ext": cmd_ext, "joint": cmd_joint, "final": cmd_final}[sys.argv[1]]()
