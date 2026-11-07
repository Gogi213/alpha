#!/usr/bin/env python3
"""TK-493, П-38 G-C2-1 и срез W2 (§10.7): правило «как в видео» (абсолютный/относительный спред, вход на пересечении, выход при возврате к базе на входе).

Новый код, написанный по описанию отчёта подсказок (docs/findings/xdl9-hints-crypto-2026-11-07.md, docstring hints_crypto.py), а не копией: база - медиана s за прошлые 1440 минут
(пересчёт кратно 5, >= 240 наблюдений; это conv_lib.basis_sigma), эпизод - серия минут с |dev| >= theta одного знака, сделка - вход на закрытии минуты пересечения против знака dev,
выход на первом общем закрытии, где s вернулся к базе НА ВХОДЕ, либо таймаут 1440 мин, либо конец периода; повторный вход - после нового пересечения. Заполнения:
g0 - закрытия; go1 - open следующего общего наблюдения; g1 - закрытие следующего общего наблюдения. Издержки: тейкер 12,6, мейкер 5,04.
  python conv_hints.py g1 --ref ~/xdl9/audit3/hints/crypto --probe [--limit N]    сверка с theta_all.csv: числа эпизодов и сделок - без расхождений, суммы - до 0,1 bps
Формирование читается через conv_guard; окно не читается (last_day <= 2478).
"""
import argparse
import csv
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import conv_guard as G      # noqa: E402
import conv_lib as L        # noqa: E402

THETAS = (10, 20, 40, 80, 160)
TIMEOUT = 1440
FEE_T, FEE_M = L.FEE_RT, L.FEE_MAKER_RT


def run_lengths(dev, theta):
    """Эпизоды по календарным минутам: серии подряд идущих минут с |dev| >= theta и неизменным знаком dev. -> (начала, длины)."""
    ok = np.isfinite(dev)
    code = np.zeros(len(dev), dtype=np.int8)
    with np.errstate(invalid="ignore"):
        hit = ok & (np.abs(dev) >= theta)
    code[hit] = np.sign(dev[hit]).astype(np.int8)
    cp = np.concatenate(([0], code, [0])).astype(np.int8)
    b = np.flatnonzero(cp[1:] != cp[:-1])
    st, en = b[:-1], b[1:]
    st = st[st < len(dev)]
    en = en[:len(st)]
    m = code[st] != 0
    return st[m], (en - st)[m]


def simulate_rule(ci, sc, dc, bc, cA, cB, oA, oB, theta):
    """Сделки правила на общих наблюдениях ci (минуты) с рядами s, dev, база. -> список (t_in, t_out, d, reason, g0, go1, g1, hold)."""
    n = len(ci)
    ok = np.isfinite(dc)
    ad = np.abs(np.where(ok, dc, 0.0))
    cross = np.zeros(n, bool)
    if n > 1:
        cross[1:] = ok[1:] & ok[:-1] & (ad[1:] >= theta) & (ad[:-1] < theta)
    cand = np.flatnonzero(cross)
    rows = []
    p = 0
    while p < len(cand):
        je = int(cand[p])
        d = -1.0 if dc[je] > 0 else 1.0
        me = bc[je]
        jt = int(np.searchsorted(ci, ci[je] + TIMEOUT, side="left"))
        hi = min(jt, n - 1)
        if hi <= je:
            break
        back = np.flatnonzero(d * (sc[je + 1:hi + 1] - me) >= 0)
        if back.size:
            jx, reason = je + 1 + int(back[0]), 0
        else:
            jx, reason = hi, (1 if jt < n else 2)
        g0 = d * ((cA[jx] / cA[je] - 1) - (cB[jx] / cB[je] - 1)) * 1e4
        go1 = g1 = float("nan")
        if jx + 1 < n:
            e1, x1 = je + 1, jx + 1
            g1 = d * ((cA[x1] / cA[e1] - 1) - (cB[x1] / cB[e1] - 1)) * 1e4
            go1 = d * ((oA[x1] / oA[e1] - 1) - (oB[x1] / oB[e1] - 1)) * 1e4
        rows.append((int(ci[je]), int(ci[jx]), int(d), reason, float(g0), float(go1), float(g1), int(ci[jx] - ci[je])))
        p = int(np.searchsorted(cand, jx, side="right"))
    return rows


def pair_table(oA, cA, oB, cB, k, thetas=THETAS):
    """Таблица правила для одной пары: по варианту (rel/abs) и theta - эпизоды и сделки (суммы по заполнениям). -> список dict."""
    s, valid = L.spread_series(cA, cB)
    base, _, _ = L.basis_sigma(s, 1440, 5, 240, None)
    ci = np.flatnonzero(valid)
    out = []
    s_adj = s - k * math.log(10) * 1e4
    for var, sv, dv, bs in (("rel", s, s - base, base), ("abs", s_adj, s_adj, np.where(valid, 0.0, np.nan))):
        for th in thetas:
            st, dur = run_lengths(dv, th)
            row = dict(var=var, theta=th, n_ep=len(dur), n_d1=int((dur == 1).sum()), n_d2=int((dur == 2).sum()), n_d3p=int((dur >= 3).sum()))
            tr = simulate_rule(ci, sv[ci], dv[ci], bs[ci], cA[ci], cB[ci], oA[ci], oB[ci], th) if len(ci) > 2 else []
            a = np.array(tr, float) if tr else np.zeros((0, 8))
            row["n"] = len(tr)
            row["n_conv"] = int((a[:, 3] == 0).sum()) if len(tr) else 0
            row["n_to"] = int((a[:, 3] == 1).sum()) if len(tr) else 0
            row["n_end"] = int((a[:, 3] == 2).sum()) if len(tr) else 0
            row["sum_g0"] = float(a[:, 4].sum()) if len(tr) else 0.0
            f1 = np.isfinite(a[:, 6]) if len(tr) else np.zeros(0, bool)
            row["n_g1"] = int(f1.sum())
            row["sum_go1"] = float(a[f1, 5].sum()) if len(tr) else 0.0
            row["sum_g1"] = float(a[f1, 6].sum()) if len(tr) else 0.0
            out.append(row)
    return out


def g1(args):
    """G-C2-1: сверка с теми же рядами, что в отчёте подсказок (theta_all.csv, строки подсказок и 'k' из pairs_all.csv)."""
    root = args.root
    ref = args.ref
    G.configure(mode="calib", params_paths=(os.path.join(HERE, "P-38-params-2026-11-07.csv"), None), script_path=os.path.join(HERE, "conv_run.py"), require_k0=not args.probe)
    pairs = list(csv.DictReader(open(os.path.join(ref, "pairs_all.csv"), encoding="utf-8")))
    tagged = [p for p in pairs if p.get("tag")] + ([] if args.tagged_only else [p for p in pairs if not p.get("tag")][:args.limit])
    theta = {}
    for r in csv.DictReader(open(os.path.join(ref, "theta_all.csv"), encoding="utf-8")):
        theta[(r["ex1"], r["sym1"], r["ex2"], r["sym2"], r["var"], int(float(r["theta"])))] = r
    bad = []
    n_rows = n_pairs = 0
    maxd = 0.0
    for p in tagged:
        fa = os.path.join(root, "panels", p["ex1"], p["sym1"] + ".sap")
        fb = os.path.join(root, "panels", p["ex2"], p["sym2"] + ".sap")
        oA, cA = G.load_panel_range(fa, 2465, 2478, what="g1")
        oB, cB = G.load_panel_range(fb, 2465, 2478, what="g1")
        tab = pair_table(oA, cA, oB, cB, int(float(p["k"] or 0)))
        n_pairs += 1
        for row in tab:
            r = theta.get((p["ex1"], p["sym1"], p["ex2"], p["sym2"], row["var"], row["theta"]))
            if r is None:
                bad.append((p["sym1"], p["sym2"], row["var"], row["theta"], "нет строки в эталоне"))
                continue
            n_rows += 1
            for c in ("n_ep", "n_d1", "n_d2", "n_d3p", "n", "n_conv", "n_to", "n_end", "n_g1"):
                if int(float(r.get(c, 0) or 0)) != row[c]:
                    bad.append((p["sym1"], p["sym2"], row["var"], row["theta"], c, r.get(c), row[c]))
            for c in ("sum_g0", "sum_go1", "sum_g1"):
                d = abs(float(r.get(c, 0) or 0) - row[c])
                maxd = max(maxd, d)
                if d > 0.1:
                    bad.append((p["sym1"], p["sym2"], row["var"], row["theta"], c, r.get(c), row[c]))
    print("G-C2-1: пар %d, строк сетки %d, расхождений %d, максимум |разность сумм| = %.3g bps" % (n_pairs, n_rows, len(bad), maxd))
    for b in bad[:15]:
        print("  ", b)
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["g1"])
    ap.add_argument("--root", default=G.HOME)
    ap.add_argument("--ref", default=os.path.join(G.HOME, "audit3", "hints", "crypto"))
    ap.add_argument("--probe", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--tagged-only", action="store_true")
    a = ap.parse_args(argv)
    G.STATE.git_checked = False
    return g1(a)


if __name__ == "__main__":
    sys.exit(main())
