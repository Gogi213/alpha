#!/usr/bin/env python3
"""TK-493 S1 (ПК): отбор A1 по эмпирическому нулю со сдвигами партнёра {+1, +2, -1} суток, в единицах РАЗЛИЧНЫХ БАЗ (П-37 r3.1 §7.2-7.3а).
Правило оценки - sel_fdrlib.py (жадное паросочетание, монотонные q, допуск k0(t*) >= 5); здесь только чтение выходов `form` и таблицы.

Вход: data/xdl9/sel/form_out/<каталог>/{real, null<k><тег>}.csv (form на Deck, alpha=1e9: статистика ВСЕХ пар; окно EG 03.10-13.10, 11 суток),
splits.csv. Тег сдвига p1/p2/m1 = +1/+2/-1 суток (sel_shift.py). Статистика пары t = max(t_first, t_second) в лучшем направлении form.
  реальные пары  - «внутри половины» хотя бы в одном из разбиений 1-3; нулевые - A x B (партнёр B сдвинут) в разбиениях 1-3 x 3 сдвига = 9 наборов;
  контроль G9 (нулевой тест) - разбиение 4 x 3 сдвига, в t* не участвует: прошло t* против ожидания k0/n0 x n0_hold (в единицах баз и пар).
Выходы: data/xdl9/sel/a1_pairs.csv (отобранные пары), docs/findings/xdl9-selection-s1-2026-11-06/{a1_catalogs.csv, a1_tstar_table.csv, g9_null_test.csv}.
"""
import csv, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402
import sel_fdrlib as F  # noqa: E402
import sel_shift as SH  # noqa: E402

TRAIN = [(k, s) for k in (1, 2, 3) for s in SH.SHIFTS]
HOLD = [(4, s) for s in SH.SHIFTS]


def load_stats(path, sid):
    """-> dict(lo, hi, t, beta, p, y, x) (массивы, без NaN-статистик); lo < hi - индексы баз."""
    y_, x_, b_, t_, p_ = [], [], [], [], []
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        next(rd)
        for y, x, beta, t1, t2, p in rd:
            t = max(float(t1), float(t2))
            if t != t:
                continue
            y_.append(sid[y]); x_.append(sid[x]); b_.append(float(beta)); t_.append(t); p_.append(float(p))
    y_, x_ = np.array(y_, dtype=np.int64), np.array(x_, dtype=np.int64)
    return dict(lo=np.minimum(y_, x_), hi=np.maximum(y_, x_), t=np.array(t_), beta=np.array(b_), p=np.array(p_), y=y_, x=x_)


def main():
    spl = {}
    with open(os.path.join(S.SEL, "splits.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            spl.setdefault(r["catalog"], {}).setdefault(int(r["split"]), {})[S.safe(r["symbol"])] = r["half"]
    base = os.path.join(S.SEL, "form_out")
    summary, picked, curve, g9 = [], [], [], []
    for cid in sorted(spl):
        if not os.path.exists(os.path.join(base, cid, "real.csv")):
            continue
        halves = spl[cid]
        syms = sorted(halves[1])
        sid = {s: i for i, s in enumerate(syms)}
        ns = len(syms)
        H = {k: np.array([1 if halves[k][s] == "B" else 0 for s in syms]) for k in halves}
        real = load_stats(os.path.join(base, cid, "real.csv"), sid)
        inside = np.zeros(len(real["t"]), dtype=bool)
        for k in (1, 2, 3):
            inside |= H[k][real["lo"]] == H[k][real["hi"]]
        rr = {a: v[inside] for a, v in real.items()}
        M_pairs = int(inside.sum())
        # нулевые наборы
        def null_sets(sets):
            out = []
            for k, s in sets:
                st = load_stats(os.path.join(base, cid, "null%d%s.csv" % (k, SH.TAG[s])), sid)
                cross = H[k][st["lo"]] != H[k][st["hi"]]
                out.append((st["lo"][cross], st["hi"][cross], st["t"][cross]))
            return out
        tr = null_sets(TRAIN)
        ho = null_sets(HOLD)
        real_idx = F.greedy_matching(rr["lo"], rr["hi"], rr["t"], ns)
        real_acc = np.sort(rr["t"][real_idx])
        null_acc = np.sort(np.concatenate([F.accepted_stats(l, h, t, ns) for l, h, t in tr])) if tr else np.zeros(0)
        res = F.select_threshold(real_acc, null_acc)
        row = dict(catalog=cid, symbols=ns, pairs_all=len(real["t"]), M_pairs=M_pairs, coverage=round(M_pairs / max(1, len(real["t"])), 4),
                   n0_pairs=int(sum(len(t) for _, _, t in tr)), M_base=res["M"], n0_base=res["n0"], status=res["status"])
        # Холм (справочно) по p всех реальных пар каталога, семья s(s-1), в порядке возрастания p
        order = np.argsort(real["p"], kind="stable")
        m_h = ns * (ns - 1)
        holm = np.zeros(len(order), dtype=bool)
        for i_, j in enumerate(order):
            if real["p"][j] <= 0.05 / (m_h - i_):
                holm[j] = True
            else:
                break
        row["holm_all"] = int(holm.sum())
        if res["M"] >= 10 and res["n0"] >= 9:
            k0c, Rc, fdr, q = F.fdr_curve(real_acc, null_acc)
            i_star = int(np.searchsorted(real_acc, res["t_star"], side="right") - 1) if res["t_star"] == res["t_star"] else len(real_acc) - 1
            lo_i, hi_i = max(0, i_star - 4), min(len(real_acc), i_star + 5)
            for i in range(lo_i, hi_i):
                curve.append([cid, "%.4f" % real_acc[i], int(Rc[i]), int(k0c[i]), "%.4f" % fdr[i], "%.4f" % q[i], int(i == i_star)])
        if res["t_star"] == res["t_star"]:
            ts = res["t_star"]
            pu = F.pair_units(rr["t"], [t for _, _, t in tr], ts)
            nl_lo = np.concatenate([l for l, _, _ in tr]); nl_hi = np.concatenate([h for _, h, _ in tr]); nl_t = np.concatenate([t for _, _, t in tr])
            row.update(t_star="%.4f" % ts, R_base=res["R"], k0_base=res["k0"], fdr="%.4f" % res["fdr"], q="%.4f" % res["q"],
                       R_pairs=pu["R_pairs"], k0_pairs=pu["k0_pairs"], k0_symbols=F.distinct_symbols(nl_lo, nl_hi, nl_t, ts))
            # контроль G9
            n0h_b = int(sum(len(F.greedy_matching(l, h, t, ns)) for l, h, t in ho))
            k0h_b = int(sum(int((F.accepted_stats(l, h, t, ns) <= ts).sum()) for l, h, t in ho))
            n0h_p = int(sum(len(t) for _, _, t in ho))
            k0h_p = int(sum(int((t <= ts).sum()) for _, _, t in ho))
            exp_b = res["k0"] / max(1, res["n0"]) * n0h_b
            exp_p = pu["k0_pairs"] / max(1, pu["n0_pairs"]) * n0h_p
            row.update(n0_hold_base=n0h_b, k0_hold_base=k0h_b, k0_hold_exp_base="%.2f" % exp_b,
                       n0_hold_pairs=n0h_p, k0_hold_pairs=k0h_p, k0_hold_exp_pairs="%.2f" % exp_p)
            g9.append([cid, "%.4f" % ts, res["n0"], res["k0"], n0h_b, k0h_b, "%.2f" % exp_b, pu["n0_pairs"], pu["k0_pairs"], n0h_p, k0h_p,
                       "%.2f" % exp_p, res["R"], res["status"]])
            if res["ok"]:
                sel = F.matched_selection(rr["lo"], rr["hi"], rr["t"], ts, ns)
                row["selected"] = len(sel)
                row["pairs_le_tstar"] = int((rr["t"] <= ts).sum())
                row["holm_selected"] = int(holm[inside][sel].sum()) if len(sel) else 0
                for j in sel:
                    picked.append([cid, syms[rr["y"][j]], syms[rr["x"][j]], "%.8g" % rr["beta"][j], "%.6f" % rr["t"][j], "%.6g" % rr["p"][j]])
            else:
                row["selected"] = 0
        else:
            row["selected"] = 0
        summary.append(row)
    hdr = ["catalog", "symbols", "pairs_all", "M_pairs", "coverage", "n0_pairs", "M_base", "n0_base", "t_star", "R_base", "k0_base", "fdr", "q",
           "R_pairs", "k0_pairs", "k0_symbols", "selected", "pairs_le_tstar", "status", "n0_hold_base", "k0_hold_base", "k0_hold_exp_base",
           "n0_hold_pairs", "k0_hold_pairs", "k0_hold_exp_pairs", "holm_all", "holm_selected"]
    S.write_csv(os.path.join(S.DOCS, "a1_catalogs.csv"), hdr, [[r.get(h, "") for h in hdr] for r in summary])
    S.write_csv(os.path.join(S.SEL, "a1_pairs.csv"), ["catalog", "y", "x", "beta", "t", "p"], picked)
    S.write_csv(os.path.join(S.DOCS, "a1_tstar_table.csv"), ["catalog", "t", "R_base", "k0_base", "fdr", "q_monotone", "is_tstar"], curve)
    S.write_csv(os.path.join(S.DOCS, "g9_null_test.csv"),
                ["catalog", "t_star", "n0_train_base", "k0_train_base", "n0_hold_base", "k0_hold_base", "k0_hold_exp_base", "n0_train_pairs",
                 "k0_train_pairs", "n0_hold_pairs", "k0_hold_pairs", "k0_hold_exp_pairs", "R_base", "status"], g9)
    print("каталогов", len(summary), "отобрано пар", len(picked), "каталогов с отбором", sum(1 for r in summary if r["status"] == "отобрано"))


if __name__ == "__main__":
    main()
