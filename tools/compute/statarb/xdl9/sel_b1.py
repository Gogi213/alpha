#!/usr/bin/env python3
"""TK-493 F7b (ПК, numpy): корзины B1 — цель против корзины из K = 3 монет с наибольшей корреляцией доходностей на ФОРМИРОВАНИИ
внутри каталога (биржа x полоса), без дублей по базовому активу (П-37 §5, план F7b). Торговое окно не читается.

Корреляция: Пирсон минутных лог-доходностей по минутам, где доходность есть у обеих ног (бары в t и t-1); не менее N_RET_MIN = 300
общих доходностей (F5, назначено без Судьи). Маска кассы США на ряды US-контрактов наложена (как в панелях Deck).
Производственный путь - матричная алгебра с масками (суммы по общим минутам через произведения матриц);
контроль G9b (--check) - прямой np.corrcoef по каждой паре на 10 случайных целях (seed 37), другой путь вычисления.
Корзина берётся, если у цели и трёх компонент не меньше N_RET_MIN общих минут с доходностью по всем четырём ногам сразу.
"""
import os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

K = 3


def returns_matrix(C, idx, sess_flags):
    """ln-доходности [s, T-1] float64, NaN где нет обоих баров; маска кассы для US-контрактов."""
    L = np.log(C[idx].astype(np.float64))
    sess = S.session_mask(C.shape[1])
    for j, us in enumerate(sess_flags):
        if us:
            L[j, ~sess] = np.nan
    return L, L[:, 1:] - L[:, :-1]


def corr_matrix(R):
    V = np.isfinite(R).astype(np.float64)
    R0 = np.where(V > 0, R, 0.0)
    n = V @ V.T
    sx = R0 @ V.T                    # [i, j] = sum_t R_i * valid_j
    sxx = (R0 * R0) @ V.T
    sxy = R0 @ R0.T
    num = n * sxy - sx * sx.T
    den = np.sqrt(np.maximum(n * sxx - sx * sx, 0.0) * np.maximum(n * sxx.T - sx.T * sx.T, 0.0))
    with np.errstate(invalid="ignore", divide="ignore"):
        c = np.where(den > 0, num / den, np.nan)
    return c, n


def build():
    rows, C, am = S.load_formation()
    ci = {(d["exchange"], d["symbol"]): i for i, d in enumerate(rows)}
    contracts = S.read_csv(os.path.join(S.SEL, "contracts.csv"))
    cats = {}
    for r in contracts:
        if r["in_catalog_a"] == "1":
            cats.setdefault(r["exchange"] + "-" + r["slug"], []).append(r)
    out, excl, cache = [], [], {}
    for cid, lst in sorted(cats.items()):
        lst.sort(key=lambda r: r["symbol"])
        if len(lst) < K + 1:
            excl.append([cid, "", "каталог меньше K+1 = %d контрактов (%d)" % (K + 1, len(lst))])
            continue
        idx = [ci[(r["exchange"], r["symbol"])] for r in lst]
        L, R = returns_matrix(C, idx, [r["us_mask"] == "1" for r in lst])
        cm, n = corr_matrix(R)
        cache[cid] = (lst, L, R)
        s = len(lst)
        cm2 = np.where(n >= S.N_RET_MIN, cm, np.nan)
        np.fill_diagonal(cm2, np.nan)
        for t in range(s):
            row = cm2[t]
            ok = np.where(np.isfinite(row))[0]
            if len(ok) < K:
                excl.append([cid, lst[t]["symbol"], "кандидатов с n_ret >= %d меньше K (%d)" % (S.N_RET_MIN, len(ok))])
                continue
            order = sorted(ok, key=lambda j: (-row[j], lst[j]["symbol"]))[:K]
            joint = np.isfinite(L[[t] + list(order)]).all(axis=0)
            jr = joint[1:] & joint[:-1]
            n_joint = int(jr.sum())
            if n_joint < S.N_RET_MIN:
                excl.append([cid, lst[t]["symbol"], "общих минут цели и трёх компонент с доходностью %d < %d" % (n_joint, S.N_RET_MIN)])
                continue
            out.append([cid, lst[t]["symbol"], S.safe(lst[t]["symbol"])] + [S.safe(lst[j]["symbol"]) for j in order]
                       + ["%.6f" % row[j] for j in order] + [n_joint])
    return out, excl, cache


def check(out, cache, n_targets=10, seed=37):
    """G9b: 10 случайных целей, прямой путь: np.corrcoef по общим минутам каждой пары, те же правила отбора."""
    rs = np.random.RandomState(seed)
    pick = rs.choice(len(out), size=min(n_targets, len(out)), replace=False)
    res, worst = [], 0.0
    for k in sorted(pick):
        row = out[k]
        cid, tgt = row[0], row[1]
        lst, L, R = cache[cid]
        syms = [r["symbol"] for r in lst]
        t = syms.index(tgt)
        cand = []
        for j in range(len(lst)):
            if j == t:
                continue
            m = np.isfinite(R[t]) & np.isfinite(R[j])
            if m.sum() < S.N_RET_MIN:
                continue
            x, y = R[t][m], R[j][m]
            if x.std() == 0 or y.std() == 0:
                continue
            cand.append((float(np.corrcoef(x, y)[0, 1]), syms[j]))
        cand.sort(key=lambda c: (-c[0], c[1]))
        top = cand[:K]
        got = [(float(row[6 + i]), syms[[S.safe(s) for s in syms].index(row[3 + i])]) for i in range(K)]
        same = [a[1] for a in top] == [b[1] for b in got]
        d = max(abs(a[0] - b[0]) for a, b in zip(top, got))
        worst = max(worst, d)
        res.append([cid, tgt, "ok" if same and d < 1e-5 else "РАСХОЖДЕНИЕ", ",".join(a[1] for a in top),
                    ",".join(b[1] for b in got), "%.2e" % d])
    return res, worst


def main():
    out, excl, cache = build()
    hdr = ["catalog", "target", "target_stem", "c1", "c2", "c3", "corr1", "corr2", "corr3", "n_joint_ret"]
    S.write_csv(os.path.join(S.SEL, "b1_baskets.csv"), hdr, out)
    S.write_csv(os.path.join(S.DOCS, "b1_excluded.csv"), ["catalog", "target", "reason"], excl)
    res, worst = check(out, cache)
    S.write_csv(os.path.join(S.DOCS, "g9b_b1_vs_numpy.csv"), ["catalog", "target", "status", "top3_direct", "top3_matrix", "max_dcorr"], res)
    bad = [r for r in res if r[2] != "ok"]
    print("корзин", len(out), "исключено целей", len(excl), "G9b: целей", len(res), "расхождений", len(bad), "макс dcorr %.2e" % worst)
    return len(bad)


if __name__ == "__main__":
    raise SystemExit(1 if main() else 0)
