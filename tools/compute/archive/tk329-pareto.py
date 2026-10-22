#!/usr/bin/env python3
"""TK-329: Парето 80 % убытка по признакам TK-327 (ret1/5/30, режим, $ стены, flow_pct, сила w20/w50, σ240).
Спот S0: мера = чистый минус Σ$ (в) (тариф В-47, как tk326-spot-loss.py). Фьюч B1 (А): Σ$ (а) групп с Σ<0 (знаменатель — сумма таких групп).
Группы — квартили по всей выборке режима (дубли порогов схлопнуты), пустые значения = группа «нет».
Использование: tk329-pareto.py <каталог findings> > выход.md"""
import bisect, csv, sys
sys.stdout.reconfigure(encoding="utf-8")
from collections import defaultdict

D = sys.argv[1] if len(sys.argv) > 1 else "docs/findings"
DATE = "2026-10-20"
FE = [("ret1_bps", "ret1 (1 с)"), ("ret5_bps", "ret5 (5 с)"), ("ret30_bps", "ret30 (30 с)"),
      ("regime_btc4h", "режим btc4h"), ("wall_usd", "$ стены"), ("flow_pct", "flow_pct"),
      ("strength_w20_pct", "сила w20"), ("strength_w50_pct", "сила w50"), ("sigma240", "σ240")]


def load(mode):
    rows = []
    with open(f"{D}/tk327-features-{mode}-{DATE}.csv", newline="") as f:
        for r in csv.DictReader(f):
            q = float(r["qty"]); px = float(r["entry_px"]); net = float(r["net_bps"])
            stop = r["reason"] in ("stop", "deadline")
            gross = net + 1.26 + (3.15 if stop else 1.26)
            k = q * px / 1e4
            r["a"] = net * k
            r["v"] = (gross - 5.625 - (6.75 if r["reason"] in ("stop", "deadline") else 5.625)) * k
            for c, _ in FE:
                r[c] = float(r[c]) if r[c] != "" else None
            rows.append(r)
    return rows


def qbin(rows, key, nb=4):
    vs = sorted(r[key] for r in rows if r[key] is not None)
    cuts = sorted({vs[int(len(vs) * i / nb)] for i in range(1, nb)})
    def f(r):
        if r[key] is None:
            return "нет"
        i = bisect.bisect_right(cuts, r[key])
        lo = "-∞" if i == 0 else f"{cuts[i-1]:.4g}"
        hi = "+∞" if i == len(cuts) else f"{cuts[i]:.4g}"
        return f"Q{i+1} [{lo}; {hi})"
    return f, cuts


def run(mode, label):
    rows = load(mode)
    N = len(rows)
    M = "v" if mode == "spot" else "a"
    if mode == "spot":
        base = rows; tot = sum(r[M] for r in rows)
        meas = "чистый минус Σ$ (в) по группам с Σ$<0"
    else:
        base = rows; tot = None
        meas = "сумма Σ$ (а) групп с Σ<0 (итог по базе +, чистого минуса нет)"
    print(f"\n### {label}: {N} сделок, Σ$ ({M}) всех = {sum(r[M] for r in rows):.0f}; мера Парето — {meas}" + (f" = {tot:.0f}" if tot is not None else "") + "\n")
    months = sorted({r["mon"] for r in rows})
    print("| признак | пороги квартилей | набор | % минуса | % сделок | лифт | мес.<0 |\n|---|---|---|---|---|---|---|")
    det = []
    for key, name in FE:
        f, cuts = qbin(rows, key)
        g = defaultdict(list)
        for r in base:
            g[f(r)].append(r)
        allg = defaultdict(list)
        for r in rows:
            allg[f(r)].append(r)
        sums = {k: sum(r[M] for r in x) for k, x in g.items()}
        if tot is None:
            T = sum(v for v in sums.values() if v < 0)
        else:
            T = tot
        neg = sorted((k for k in sums if sums[k] < 0), key=lambda k: sums[k])
        sel, cum = [], 0.0
        for k in neg:
            sel.append(k); cum += sums[k]
            if cum / T >= 0.8:
                break
        nsel = sum(len(allg[k]) for k in sel)
        mm = []
        for m in months:
            ms = sum(r[M] for k in sel for r in allg[k] if r["mon"] == m)
            mm.append((m, sum(1 for k in sel for r in allg[k] if r["mon"] == m), ms))
        mneg = sum(1 for _, _, s in mm if s < 0)
        nn = sum(1 for r in rows if r[key] is None)
        lift = (cum / T) / (nsel / N) if nsel else 0
        short = ", ".join(sel) if len(sel) <= 3 else f"{len(sel)} из {len(g)} групп"
        print(f"| {name}{f' (нет у {nn})' if nn else ''} | {', '.join(f'{c:.4g}' for c in cuts)} | {short} | {100*cum/T:.0f}% | {100*nsel/N:.0f}% | {lift:.2f} | {mneg}/{len(months)} |")
        det.append((name, mm))
        # группы целиком
        print("") if False else None
    print("\nПо группам (n · Σ$ " + M + "):\n")
    for key, name in FE:
        f, _ = qbin(rows, key)
        g = defaultdict(list)
        for r in rows:
            g[f(r)].append(r)
        print(f"- **{name}**: " + "; ".join(f"{k}: {len(x)} · {sum(r[M] for r in x):.0f}" for k, x in sorted(g.items())))
    print("\nПомесячно (Σ$ набора, n набора; месяцы с положительным знаком — +):\n")
    for name, mm in det:
        print(f"- **{name}**: " + "; ".join(f"{m[5:]}: {s:.0f} (n {n})" for m, n, s in mm))


print(f"## TK-329: Парето 80 % по признакам TK-327 (описание, не правило; срезы выбраны по тем же данным)\n")
run("spot", "Спот S0")
run("fut", "Фьюч B1, половина А")
