#!/usr/bin/env python3
"""TK-493 F7 шаг 3 (ПК): отбор A1 по эмпирическому нулю со сдвигом партнёра на +1 сутки (П-37 §7).

Вход: data/xdl9/sel/form_out/<каталог>/{real,null1..4}.csv (form на Deck, alpha=1e9: статистика ВСЕХ пар) и splits.csv.
Статистика пары t = max(t_first, t_second) в лучшем направлении (в form направление выбирается по меньшему p; p монотонно по t,
поэтому max(t) эквивалентно p = max(p1, p2) из select.rs, но без усечения p в хвосте).
  реальные пары  - внутри половины A или внутри B хотя бы в одном из разбиений 1-3 (M - число различных пар);
  нулевые пары   - A x B в разбиениях 1-3, партнёр B сдвинут на +1 сутки (n0 - сумма по разбиениям);
  FDR(t) = (k0(t)+1)/(n0+1) * M / max(1, R(t)); t* - наименее строгий t с FDR(t) <= q (q = 0,10, Н7).
Контроль G9 (нулевой тест): разбиение 4 (в t* не участвует), сдвиг +1 сутки: доля нулевых пар со статистикой <= t*.
Выходы: data/xdl9/sel/a1_pairs.csv (отобранные пары), docs/findings/xdl9-selection-2026-11-06/a1_catalogs.csv (по каталогам).
"""
import csv, os, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sel_lib as S  # noqa: E402

Q = 0.10


def load_stats(path):
    """-> {(y, x): (t, p, beta)} по неупорядоченной паре; ключ - (min, max) имён."""
    out = {}
    with open(path, encoding="utf-8", newline="") as fh:
        rd = csv.reader(fh)
        next(rd)
        for y, x, beta, t1, t2, p in rd:
            t = max(float(t1), float(t2))
            if t != t:
                continue
            out[(y, x)] = (t, float(p), float(beta))
    return out


def main():
    spl = {}
    with open(os.path.join(S.SEL, "splits.csv"), encoding="utf-8", newline="") as fh:
        for r in csv.DictReader(fh):
            spl.setdefault(r["catalog"], {}).setdefault(int(r["split"]), {})[S.safe(r["symbol"])] = r["half"]
    base = os.path.join(S.SEL, "form_out")
    summary, picked = [], []
    for cid in sorted(spl):
        real = load_stats(os.path.join(base, cid, "real.csv"))
        halves = spl[cid]
        syms = sorted(halves[1])
        s = len(syms)
        # реальные пары: объединение «внутри половины» по разбиениям 1-3
        real_pairs = {}
        for (y, x), v in real.items():
            for k in (1, 2, 3):
                h = halves[k]
                if h[y] == h[x]:
                    real_pairs[(y, x)] = v
                    break
        M = len(real_pairs)
        rs = np.sort(np.array([v[0] for v in real_pairs.values()])) if M else np.array([])
        # нулевые пары: A x B в разбиениях 1-3 и 4 (отдельно)
        ns, n0_per = [], {}
        for k in (1, 2, 3, 4):
            st = load_stats(os.path.join(base, cid, "null%d.csv" % k))
            h = halves[k]
            ts = [v[0] for (y, x), v in st.items() if h[y] != h[x]]
            n0_per[k] = ts
            if k <= 3:
                ns.extend(ts)
        ns = np.sort(np.array(ns))
        n0 = len(ns)
        row = dict(catalog=cid, symbols=s, pairs_all=len(real), M=M, coverage=round(M / max(1, len(real)), 4), n0=n0)
        if M < 10 or n0 < 9:
            row.update(t_star="", R="", k0="", fdr="", selected=0, status="нет отбора: мало пар (M=%d, n0=%d)" % (M, n0),
                       n0_hold="", k0_hold="", holm_all="", holm_selected="")
            summary.append(row)
            continue
        R = np.searchsorted(rs, rs, side="right")
        k0 = np.searchsorted(ns, rs, side="right")
        fdr = (k0 + 1.0) / (n0 + 1.0) * M / np.maximum(1, R)
        ok = np.where(fdr <= Q)[0]
        # holm справочно: по p из real.csv (все пары каталога) в порядке возрастания p
        order = sorted(real.items(), key=lambda kv: kv[1][1])
        m_h = s * (s - 1)
        holm_keys = set()
        for i_, (k_, v_) in enumerate(order):
            if v_[1] <= 0.05 / (m_h - i_):
                holm_keys.add(k_)
            else:
                break
        row["holm_all"] = len(holm_keys)
        if len(ok) == 0:
            row.update(t_star="", R="", k0="", fdr="%.4f" % fdr.min(), selected=0, status="нет отбора: FDR(t) > %.2f при любом t" % Q,
                       n0_hold=len(n0_per[4]), k0_hold="", holm_selected="")
            summary.append(row)
            continue
        i_star = ok[-1]
        t_star = rs[i_star]
        sel = [(k, v) for k, v in real_pairs.items() if v[0] <= t_star]
        ts4 = np.array(n0_per[4])
        k0_hold = int((ts4 <= t_star).sum())
        row.update(t_star="%.4f" % t_star, R=int(R[i_star]), k0=int(k0[i_star]), fdr="%.4f" % fdr[i_star], selected=len(sel),
                   status="отобрано", n0_hold=len(ts4), k0_hold=k0_hold, holm_selected=len([1 for k, _ in sel if k in holm_keys]))
        summary.append(row)
        for (y, x), v in sorted(sel, key=lambda kv: kv[1][0]):
            picked.append([cid, y, x, "%.8g" % v[2], "%.6f" % v[0], "%.6g" % v[1]])
    hdr = ["catalog", "symbols", "pairs_all", "M", "coverage", "n0", "t_star", "R", "k0", "fdr", "selected", "status", "n0_hold",
           "k0_hold", "holm_all", "holm_selected"]
    S.write_csv(os.path.join(S.DOCS, "a1_catalogs.csv"), hdr, [[r.get(h, "") for h in hdr] for r in summary])
    S.write_csv(os.path.join(S.SEL, "a1_pairs.csv"), ["catalog", "y", "x", "beta", "t", "p"], picked)
    print("каталогов", len(summary), "отобрано пар", len(picked))


if __name__ == "__main__":
    main()
