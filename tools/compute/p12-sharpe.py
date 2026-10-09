#!/usr/bin/env python3
"""TK-063: П-12 §9 по В-208 (Судья reviews/P-12-2026-10-08-sharpe-rule.md) — ΔSR к базе, R1 (154 кл.) + R2 v2 (40 кл.) одним анализом.
Ряд — $_норм по суткам закрытия (closes cap0 / cap3 для B2); загрузка — головы p12-r1-analyze.py и p12-r2-analyze.py (exec до бутстрепа).
Ворота: п.1 ΔSR>0 (парный блочный бутстреп, блоки общие, max-T WY в семье, Холм m=42, p<=α_k/2); п.2 С3 по месяцам (верх 95% ΔSR_мес>=0);
п.3 С2 (без 2 суток с наибольшей парной разностью; монета — closes без символа, считается для прошедших отдельно); п.4 >=6 из 8 месяцев.
Порядок: ярусы по SR (бутстреп лидер−i, Холм, α=0,05), затем k_c, K_c, SR. Выход docs/findings/p12-sharpe-<free|B2>-2026-10-08.csv + печать."""
import csv, math, os, sys
from collections import defaultdict
import numpy as np

MODE = sys.argv[1] if len(sys.argv) > 1 else "free"
M_FAM, B = 42, 20000
POOL = {f"2026-{i:02d}" for i in range(int(os.environ.get("P12_POOL_FROM", 2)), 10)}   # В-211: окно янв–сен — P12_POOL_FROM=1
ONLY_R2 = os.environ.get("P12_ONLY") in ("r2", "r1r2")   # r1r2 — R1 и R2 без ext   # v171c: R1 и ext на пуле ещё не пересчитаны (TK-113) — только R2
OUT = os.environ.get("P12_OUT", "2026-10-08")


def load(path, mode):
    src = open(path, encoding="utf-8").read()
    cut = src.index("S = {c: series(c) for c in raw}") if "p12-r1" in path else src.index("rng = np.random.default_rng(63)")
    sys.argv = ["x", mode]
    ns = {"__name__": "x"}
    exec(compile(src[:cut], path, "exec"), ns)
    return ns


def pack(ns, r1):
    """-> {имя: dict(arr, lst{мес:[(мс,$)]}, dfn[мес], fam, base, cal)}"""
    out = {}
    if r1:
        S = {c: ns["series"](c) for c in ns["raw"]}
        for c, s in S.items():
            out[c] = dict(arr=s[0], lst=ns["raw"][c], dfn=set(ns["defined_of"](s[1], s[2])), fam=c.split("-")[0] if c != "B1" else "B1",
                          base=None if c == "B1" else "B1", cal=ns["cal"], months=ns["MONTHS"], kpi=ns["kpi_share"])
    else:
        S = ns["S"]
        for c, s in S.items():
            f = ns["CELLS"][c][0] if c in ns["CELLS"] else c
            base = None if c in ("B1", "B3", "B1g") else ns["BN"][ns["CELLS"][c][1]]
            out[c] = dict(arr=s[0], lst=s[3], dfn=set(ns["defined_of"](s[1], s[2], s[4] if len(s) > 4 else None)), fam=f, base=base,
                          cal=ns["cal"], months=ns["MONTHS"], kpi=ns["kpi_share"])
    return out


def pack_ext(mode):
    """ext-клетки TK-083 (7 кл., x-stop/x-deadline/x-entry): closes data/tk083/kpi/closes[B2]-<мес>.json; $ без нормировки (t2 ≤ 1,25 × B1, вердикт ext)."""
    path = "tools/compute/tk083-kpi-analyze.py"
    src = open(path, encoding="utf-8").read()
    sys.argv = ["x", "" if mode == "free" else "B2"]
    ns = {"__name__": "x"}
    exec(compile(src[:src.index("rng = np.random.default_rng(83)")], path, "exec"), ns)
    out = {}
    for c, a in ns["daily"].items():
        out[c] = dict(arr=a, lst=ns["cl"][c], dfn=set(ns["defined"][c]), fam="B1" if c == "B1" else ns["CELLS"][c][0], base=None if c == "B1" else "B1",
                      cal=ns["cal"], months=ns["MONTHS"], kpi=lambda l, m, k=ns["kpi_share"]: (lambda r: (r[0], r[2]))(k(l, m)))
    return out


def sr(x, m):
    n = m.sum(-1)
    mean = (x * m).sum(-1) / np.maximum(n, 1)
    var = (((x - mean[..., None]) ** 2) * m).sum(-1) / np.maximum(n - 1, 1)
    sd = np.sqrt(var)
    return np.where(sd > 0, mean / np.where(sd > 0, sd, 1), 0.0)


def block_idx(rng, n):
    L = max(1, math.ceil(n ** (1 / 3))); nb = math.ceil(n / L)
    return ((rng.integers(0, n, size=(B, nb))[:, :, None] + np.arange(L)) % n).reshape(B, -1)[:, :n]


def analyse(P, seed):
    rng = np.random.default_rng(seed)
    cal0 = next(iter(P.values()))["cal"]; months = [m for m in next(iter(P.values()))["months"] if m in POOL]
    keep = np.array([d[:7] in POOL for d in cal0])   # §4: пул счёта фев–сен; январь — калибровка, октябрь — неполный
    cal = [d for d, k in zip(cal0, keep) if k]; n = len(cal)
    mon = np.array([d[:7] for d in cal])
    for p in P.values():
        p["arr"] = p["arr"][keep]; p["dfn"] = p["dfn"] & POOL
    idx = block_idx(rng, n)
    midx = {}
    for m in months:
        pos = np.where(mon == m)[0]
        if len(pos):
            midx[m] = (pos, pos[block_idx(rng, len(pos))])
    res = {}
    for c, p in P.items():
        if p["base"] is None:
            continue
        b = P[p["base"]]
        dm = p["dfn"] & b["dfn"]
        mk = np.isin(mon, list(dm))
        if mk.sum() < 20:
            res[c] = None; continue
        x, y = p["arr"], b["arr"]
        sc, sb = float(sr(x, mk.astype(float))), float(sr(y, mk.astype(float)))
        xs, ys, ms = x[idx], y[idx], mk[idx].astype(float)
        d = sr(xs, ms) - sr(ys, ms); dsr = sc - sb
        se = d.std(ddof=1) or 1e-9
        r = dict(c=c, fam=p["fam"], base=p["base"], mk=mk, dm=dm, sr=sc, srb=sb, dsr=dsr, se=se, T=dsr / se, Ts=(d - dsr) / se,
                 lo=np.percentile(d, 2.5), hi=np.percentile(d, 97.5))
        mres = {}
        for m in sorted(dm):
            pos, mi = midx[m]
            ones = np.ones(mi.shape)
            dd = sr(x[mi], ones) - sr(y[mi], ones)
            one = np.ones(len(pos))
            mres[m] = (float(sr(x[pos], one) - sr(y[pos], one)), np.percentile(dd, 97.5), float(sr(x[pos], one)))
        r["mon"] = mres
        r["k"] = sum(1 for m in mres if mres[m][2] > 0)
        diff = np.where(mk, x - y, -1e18); top = np.argsort(-diff)[:2]
        mk2 = mk.copy(); mk2[top] = False
        s2c, s2b = sr(x, mk2.astype(float)), sr(y, mk2.astype(float))
        m2 = mk2[idx].astype(float)
        d2 = sr(xs, m2) - sr(ys, m2)
        r["c2"] = (float(s2c - s2b), np.percentile(d2, 2.5))
        w = 0; bad = 0.0
        for m in sorted(dm):
            s, _eq = p["kpi"](p["lst"].get(m, []), m); wt = (mon == m).sum(); bad += s * wt; w += wt
        r["K"] = bad / max(w, 1)
        r["usd"] = sum(v for m in dm for _a, v in p["lst"].get(m, [])); r["usdb"] = sum(v for m in dm for _a, v in b["lst"].get(m, []))
        res[c] = r
    ok = [c for c in res if res[c]]
    fam = defaultdict(list)
    for c in ok:
        fam[res[c]["fam"]].append(c)
    for f, cs in fam.items():
        for key, sgn in (("pwy", -1), ("pharm", 1)):
            order = sorted(cs, key=lambda c: sgn * res[c]["T"]); prev = 0.0
            for k, c in enumerate(order):
                ts = np.array([res[o]["Ts"] for o in order[k:]])
                pv = float(((ts.max(0) >= res[c]["T"]) if sgn < 0 else (ts.min(0) <= res[c]["T"])).mean())
                prev = max(prev, pv); res[c][key] = prev
    return res, fam


def main():
    A, fa = ({}, {}) if os.environ.get("P12_ONLY") == "r2" else analyse(pack(load("tools/compute/p12-r1-analyze.py", MODE), True), 63)
    Bz, fb = analyse(pack(load("tools/compute/p12-r2-analyze.py", MODE), False), 64)
    Ex, fe = ({}, {}) if ONLY_R2 else analyse(pack_ext(MODE), 65)
    res = {**{c: dict(r, pk="R1") for c, r in A.items() if r}, **{c: dict(r, pk="R2") for c, r in Bz.items() if r}, **{c: dict(r, pk="ext") for c, r in Ex.items() if r}}
    fams = {**fa, **fb, **fe}
    famp = {f: min(res[c]["pwy"] for c in cs) for f, cs in fams.items()}
    famh = {f: min(res[c]["pharm"] for c in cs) for f, cs in fams.items()}
    holm = {}
    for store, nm in ((famp, "польза"), (famh, "вред")):
        alive = True
        for k, (f, p) in enumerate(sorted(store.items(), key=lambda kv: kv[1])):
            thr = 0.025 / (M_FAM - k); alive = alive and p <= thr; holm[(nm, f)] = (p, thr, alive)
    print(f"== П-12 Шарп (В-208), режим {MODE}; клеток R1 {sum(1 for r in res.values() if r['pk']=='R1')} R2 {sum(1 for r in res.values() if r['pk']=='R2')} ext {sum(1 for r in res.values() if r['pk']=='ext')}; семей {len(fams)}, m={M_FAM}")
    rows = []
    for c, r in res.items():
        g1 = bool(holm[("польза", r["fam"])][2] and r["pwy"] <= holm[("польза", r["fam"])][1])
        bad_m = [m for m, v in r["mon"].items() if v[1] < 0]
        g2 = not bad_m
        g3 = bool(r["c2"][0] >= 0 and r["c2"][1] >= 0)
        g4 = len(r["dm"]) >= 6
        passed = g1 and g2 and g3 and g4
        refut = bool(holm[("вред", r["fam"])][2] and r["pharm"] <= holm[("вред", r["fam"])][1])
        fails = [i for i, g in ((1, g1), (2, g2), (3, g3), (4, g4)) if not g]
        r.update(g1=g1, g2=g2, g3=g3, g4=g4, passed=passed, refut=refut, bad_m=bad_m)
        rows.append([MODE, r["pk"], c, r["fam"], r["base"], round(r["sr"], 4), round(r["sr"] * math.sqrt(365), 2), round(r["srb"], 4), round(r["dsr"], 4),
                     round(r["lo"], 4), round(r["hi"], 4), round(r["T"], 3), round(r["pwy"], 4), round(r["pharm"], 4), r["k"], len(r["dm"]), round(r["K"], 4),
                     round(r["usd"] - r["usdb"], 1), int(g1), int(g2), int(g3), int(g4), int(passed), int(refut), ";".join(map(str, fails)),
                     ";".join(bad_m), round(r["c2"][0], 4), round(r["c2"][1], 4)])
    print("семьи (польза/вред), p < 0,3: порог Холма α/2/(42-k)")
    for (nm, f), (p, thr, ok) in sorted(holm.items(), key=lambda kv: (kv[0][0], kv[1][0])):
        if p < 0.3:
            print(f"  {nm} {f}: p={p:.4f} thr={thr:.2e} {'ПРОХОДИТ' if ok else 'нет'}")
    print("прошли ворота:", [c for c, r in res.items() if r["passed"]])
    print("опровергнуто (вред п.1):", [c for c, r in res.items() if r["refut"]])
    for key, nm in (("g1", "п.1"), ("g2", "п.2"), ("g3", "п.3"), ("g4", "п.4")):
        print(nm, "проходят:", sum(1 for r in res.values() if r[key]))
    for r in sorted(res.values(), key=lambda r: -r["T"])[:10]:
        print(f"топ T: {r['c']} SR{r['sr']:+.3f} (B {r['srb']:+.3f}) ΔSR{r['dsr']:+.3f}[{r['lo']:+.3f};{r['hi']:+.3f}] T{r['T']:.2f} pWY{r['pwy']:.3f} k={r['k']}/{len(r['dm'])} K={r['K']:.3f}")
    for r in sorted(res.values(), key=lambda r: r["T"])[:6]:
        print(f"низ T: {r['c']} SR{r['sr']:+.3f} (B {r['srb']:+.3f}) ΔSR{r['dsr']:+.3f}[{r['lo']:+.3f};{r['hi']:+.3f}] T{r['T']:.2f} pharm{r['pharm']:.3f}")
    print("ΔSR>0:", sum(r["dsr"] > 0 for r in res.values()), "из", len(res), "; нижняя 95%>0:", sum(r["lo"] > 0 for r in res.values()),
          "; верх<0:", sum(r["hi"] < 0 for r in res.values()))
    ranked = sorted(res.values(), key=lambda r: (-r["k"], r["K"], -r["sr"]))
    print("справочно (k_c, K_c, SR; без ярусов и ворот):", [(r["c"], r["k"], round(r["K"], 3), round(r["sr"], 3)) for r in ranked[:5]])
    with open(f"docs/findings/p12-sharpe-{MODE}-{OUT}.csv", "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["mode", "pack", "cell", "family", "base", "sr_day", "sr_year", "sr_base", "d_sr", "ci_lo", "ci_hi", "T", "p_wy", "p_harm_wy", "k_months_sr_pos",
                    "months_defined", "kpi_share", "d_usd", "gate1", "gate2", "gate3", "gate4", "passed", "refuted", "failed_gates", "months_dsr_hi_neg", "c2_dsr", "c2_lo"])
        w.writerows(sorted(rows, key=lambda r: -r[11]))


main()
