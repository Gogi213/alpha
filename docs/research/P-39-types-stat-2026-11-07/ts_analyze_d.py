#!/usr/bin/env python3
"""Анализ D (мощность) и сверка воспроизведения V1 / V2 (588 / 613 сделок окна, 356 / 359 формирования) с файлами репозитория.

y_d = [Σ net закрытых в сутки d + MTM (последние сутки)] / число активных пар-суток d (bps на пару-сутки), как cell_status.build_series. Блок ⌈n^{1/3}⌉, n_эфф = ⌈n/блок⌉, df = n_эфф - 1.
МРЭ: (t_{0,95,df} + z_{0,80})·sd/√n_эфф (согласовано с «нижняя граница 90 %-интервала > 0»); протокольная версия 2,8·sd/√n_эфф. Перевод в bps на сделку: делением на число сделок на активную пару-сутки.
"""
import argparse
import csv
import math
import os
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ts_stats as S       # noqa: E402

D0 = 2465
CODE_END = 7
HALF_DAYS = {0: (2466, 2478), 1: (2479, 2499)}


def yd_series(tr, act, ui, hv):
    """Суточный ряд y_d (закрытые + MTM) для универсума ui и половины hv. -> (дни, y, n_сделок, Σ активных пар-суток, Σnet, закрытых)."""
    d1, d2 = HALF_DAYS[hv]
    m = (tr["uni"] == ui) & (tr["half"] == hv)
    f_out = tr["f_out"][m].astype(np.int64)
    net = tr["net"][m]
    mtm = tr["mtm"][m] > 0
    day_out = D0 + f_out // 1440
    a = act[(act[:, 0] == ui) & (act[:, 1] == hv)]
    den = {int(r[2]): int(r[3]) for r in a}
    days = [d for d in range(d1, d2 + 1) if den.get(d, 0) > 0]
    num = {d: float(net[(~mtm) & (day_out == d)].sum()) for d in days}
    y = {d: num[d] / den[d] for d in days}
    mtm_sum = float(net[mtm].sum())
    if mtm_sum != 0.0 or mtm.any():
        last = d2
        den_last = den.get(last, 0) if den.get(last, 0) > 0 else int(mtm.sum())
        if last in y:
            y[last] += mtm_sum / den_last
        else:
            y[last] = mtm_sum / den_last
            days.append(last)
    days = sorted(y)
    return days, np.array([y[d] for d in days]), int(m.sum()), int(sum(den.get(d, 0) for d in range(d1, d2 + 1))), float(net.sum()), int((~mtm).sum())


def block_interval(series, alpha):
    P = S.portfolio()
    mean, lo, hi, p, df = P.block_t_interval(list(series), alpha)
    return mean, lo, hi, df


def needed_days(sd, delta_y, power, n_max=400, q=0.95):
    """Наименьшее число суток n, при котором МРЭ(n) <= delta_y (sd - фактическое SD суточного ряда, блок ⌈n^{1/3}⌉)."""
    for n in range(3, n_max + 1):
        md = S.mde_table(sd, n, power=(power if power is not None else 0.5), q=q)
        v = md["mde_power"]
        if np.isfinite(v) and v <= delta_y:
            return n
    return None


def read_repo_trades(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    return rows


def replicate_check(tr, repo_dir, outdir):
    """Сверка моих V1 / V2 с trades.csv окна и формирования (число сделок, Σ net, совпадение (pid, t_sig, f_out, net))."""
    out = []
    ref = {("V1", 1): os.path.join(repo_dir, "docs/research/P-38-window-2026-11-07/grid_V1/trades.csv"), ("V2", 1): os.path.join(repo_dir, "docs/research/P-38-window-2026-11-07/grid_V2/trades.csv"),
           ("V1", 0): os.path.join(repo_dir, "docs/research/P-38-window-2026-11-07/formation_ref/formation_V1_trades.csv"),
           ("V2", 0): os.path.join(repo_dir, "docs/research/P-38-window-2026-11-07/formation_ref/formation_V2_trades.csv")}
    pairs_json = None
    for (un, hv), path in ref.items():
        if not os.path.isfile(path):
            continue
        ui = {"V1": 2, "V2": 3}[un]
        rows = read_repo_trades(path)
        ref_map = {(int(r["t_sig"]), int(r["f_out"])): float(r["net"]) for r in rows}
        m = (tr["uni"] == ui) & (tr["half"] == hv)
        me_map = {(int(t), int(f)): float(n) for t, f, n in zip(tr["t_sig"][m], tr["f_out"][m], tr["net"][m])}
        same_keys = set(ref_map) == set(me_map)
        maxd = max((abs(ref_map[k] - me_map[k]) for k in ref_map if k in me_map), default=float("nan"))
        out.append(dict(uni=un, half="F" if hv == 0 else "W", n_ref=len(rows), n_me=int(m.sum()), sum_net_ref=sum(float(r["net"]) for r in rows), sum_net_me=float(tr["net"][m].sum()),
                        equal_keys=same_keys, max_abs_diff_net=maxd))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tables", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repo", required=True)
    a = ap.parse_args()
    tr, ep, hints, act, meta = S.load_tables(a.tables)
    os.makedirs(a.out, exist_ok=True)
    rep = replicate_check(tr, a.repo, a.out)
    with open(os.path.join(a.out, "D_replicate.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rep[0].keys()), delimiter=";")
        w.writeheader()
        w.writerows(rep)
    for r in rep:
        print(r)
    rows = []
    for un, ui in (("V1", 2), ("V2", 3), ("B1", 0), ("B3", 1)):
        for hv in (0, 1):
            days, y, ntr, nact, snet, ncl = yd_series(tr, act, ui, hv)
            if len(y) < 3:
                continue
            sd = float(np.std(y, ddof=1))
            mean, lo, hi, df = block_interval(y, 0.10)
            n = len(y)
            r_tpd = ntr / nact if nact else float("nan")
            md = S.mde_table(sd, n)
            rows.append(dict(uni=un, half="F" if hv == 0 else "W", days=n, trades=ntr, active_pair_days=nact, trades_per_apd=r_tpd, y_mean=mean, y_lo=lo, y_hi=hi, sd=sd, n_eff=md["n_eff"], df=md["df"],
                             mde50=md["mde50"], mde_power80=md["mde_power"], mre_protocol=md["mre_protocol"], net_trade=snet / max(ntr, 1)))
    with open(os.path.join(a.out, "D_yd.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()), delimiter=";")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("%.6g" % v if isinstance(v, float) else v) for k, v in r.items()})
    for r in rows:
        print({k: (round(v, 5) if isinstance(v, float) else v) for k, v in r.items()})
    # мощность: сколько суток нужно, чтобы поймать net +5 и +10 bps на сделку; МРЭ при 21 сутках в bps на сделку
    prow = []
    byk = {(r["uni"], r["half"]): r for r in rows}
    for un, ui in (("V1", 2), ("V2", 3), ("B1", 0), ("B3", 1)):
        ys = {}
        for hv in (0, 1):
            days, y, ntr, nact, snet, ncl = yd_series(tr, act, ui, hv)
            ys[hv] = y
        sds = {"SD окна": float(np.std(ys[1], ddof=1)), "SD формирования": float(np.std(ys[0], ddof=1)), "SD обеих половин (35 суток)": float(np.std(np.concatenate([ys[0], ys[1]]), ddof=1))}
        tpd = byk[(un, "W")]["trades_per_apd"]
        for sdn, sd in sds.items():
            m21 = S.mde_table(sd, 21)
            row = dict(uni=un, sd_label=sdn, sd=sd, trades_per_apd=tpd, mde50_21_bps=m21["mde50"] / tpd, mde80_21_bps=m21["mde_power"] / tpd, mre_protocol_21_bps=m21["mre_protocol"] / tpd)
            m21h = S.mde_table(sd, 21, q=0.975)
            row["mde80_21_bps_holm95"] = m21h["mde_power"] / tpd
            for dl in (5, 10):
                for pw, pwn in ((None, "50"), (0.80, "80")):
                    nn = needed_days(sd, dl * tpd, pw)
                    row["days_net%d_p%s" % (dl, pwn)] = nn
                row["days_net%d_p80_holm95" % dl] = needed_days(sd, dl * tpd, 0.80, q=0.975)
            prow.append(row)
    with open(os.path.join(a.out, "D_power.csv"), "w", encoding="utf-8", newline="") as f:
        cols = list(prow[0].keys())
        w = csv.DictWriter(f, fieldnames=cols, delimiter=";")
        w.writeheader()
        for r in prow:
            w.writerow({k: ("%.6g" % v if isinstance(v, float) else v) for k, v in r.items()})
    for r in prow:
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})
    # n_эфф по активам: Kish по числу сделок, плотность ICC (ANOVA) и доля лучших активов
    arow = []
    for un, ui in (("V1", 2), ("V2", 3), ("B1", 0), ("B3", 1)):
        for hv in (0, 1):
            m = (tr["uni"] == ui) & (tr["half"] == hv) & (tr["mtm"] == 0)
            ai, net = tr["ai"][m].astype(int), tr["net"][m]
            if m.sum() < 10:
                continue
            ua, inv = np.unique(ai, return_inverse=True)
            cnt = np.bincount(inv).astype(float)
            sm = np.bincount(inv, weights=net)
            n, k = len(net), len(ua)
            mean = net.mean()
            ssb = float((cnt * (sm / cnt - mean) ** 2).sum())
            ssw = float(((net - (sm / cnt)[inv]) ** 2).sum())
            msb, msw = ssb / max(k - 1, 1), ssw / max(n - k, 1)
            m0 = (n - float((cnt ** 2).sum()) / n) / max(k - 1, 1)
            icc = max((msb - msw) / (msb + (m0 - 1) * msw), 0.0) if (msb + (m0 - 1) * msw) > 0 else 0.0
            mbar = n / k
            deff = 1 + (mbar - 1) * icc
            srt = np.sort(np.abs(sm))[::-1]
            arow.append(dict(uni=un, half="F" if hv == 0 else "W", trades=n, assets=k, kish_neff_trades=float(n * n / (cnt ** 2).sum()), icc=icc, deff=deff, neff_icc=n / deff,
                             top10_share_abs_net=float(srt[:10].sum() / max(srt.sum(), 1e-12)), top1_share_abs_net=float(srt[0] / max(srt.sum(), 1e-12))))
    with open(os.path.join(a.out, "D_neff_assets.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(arow[0].keys()), delimiter=";")
        w.writeheader()
        for r in arow:
            w.writerow({k: ("%.6g" % v if isinstance(v, float) else v) for k, v in r.items()})
    for r in arow:
        print({k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()})


if __name__ == "__main__":
    main()
