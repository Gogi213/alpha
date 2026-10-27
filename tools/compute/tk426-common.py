#!/usr/bin/env python3
"""TK-426 вопрос А «общее у пампов»: признак×фаза×знак против подобранных C1 (P-TK426 v2 §6-7).
Статистика: δ Клиффа (парный) Δ(e)=f(e)-медиана f по 3 C1; p — перестановкой внутри четвёрки; интервал — бутстреп по монетам;
BH по всем ячейкам; разрезы (биржа / половина месяца / монеты пополам); MDE — 95-й перцентиль |δ| из тех же перестановок.
Вход: признаки TK-422 v5, контроли C1, события TK-421 (кластер_id, монета_id), список подтверждённых (--confirmed; без него — ПРОБА).
"""
import argparse, re, sys, zlib
import numpy as np, pandas as pd

BY_DEF = re.compile(r"^(btc_ret_bps_leg|mins_.*|T_min|ret_bps_leg|range_bps_leg|leg_.*|end_pos_in_leg.*|retrace_.*|"
                    r"sw_.*(swept_n|top_over_bps|ret_N\d+_T\d+)_leg|htf_.*(over_bps_leg|ret_.*_leg|dfr_n_leg)|"
                    r"body_hold_closes_.*|amd_.*(break|manip_).*|smt_.*(swept_leg|diverge_leg)|fvg_.*|abs_N\d+_disp_bps_leg|"
                    r"flow_delta_per_bps_leg|spot_leg_bps|spot_minus_fut_leg_bps|basis_peak_.*|btc_decorr_leg|btc_corr_leg)$")
META = {"id", "биржа", "рынок", "инструмент", "t_start_ms", "t_peak_ms", "t_end_ms", "sign", "leg_min", "pull_min", "hist_min"}
SEP1 = 1788220800000  # 2026-09-01 UTC
HALF = SEP1 + 15 * 86400000


def bh(p, q):
    m = len(p); o = np.argsort(p); adj = np.empty(m); run = 1.0
    for r in range(m - 1, -1, -1):
        run = min(run, p[o[r]] * m / (r + 1)); adj[o[r]] = run
    return adj


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--features", required=True); ap.add_argument("--controls", required=True)
    ap.add_argument("--events", required=True); ap.add_argument("--labels")
    ap.add_argument("--confirmed", help="csv с колонкой id (подтверждённые «памп»); без него — ПРОБА по всем кроме меток «не памп»")
    ap.add_argument("--out", required=True); ap.add_argument("--perm", type=int, default=10000)
    ap.add_argument("--boot", type=int, default=1000); ap.add_argument("--q", type=float, default=0.10)
    ap.add_argument("--list-by-definition", action="store_true"); ap.add_argument("--seed", type=int, default=426)
    a = ap.parse_args()
    f = pd.read_csv(a.features, low_memory=False)
    cols = [c for c in f.columns if c not in META and not c.startswith("cov_") and c.endswith(("_pre", "_leg", "_ctx"))]
    by_def = [c for c in cols if BY_DEF.match(c)]
    if a.list_by_definition:
        print("\n".join(by_def)); return
    test_cols = [c for c in cols if c not in by_def]
    ev = pd.read_csv(a.events); ctl = pd.read_csv(a.controls)
    probe = a.confirmed is None
    if probe:
        ok = set(ev.id)
        if a.labels:
            lb = pd.read_csv(a.labels); ok -= set(lb.loc[lb.метка.astype(str).str.contains("не", na=False), "id"])
    else:
        ok = set(pd.read_csv(a.confirmed).id)
    ev = ev[ev.id.isin(ok)].set_index("id")
    f = f.drop_duplicates("id").set_index("id")
    X = f[test_cols].apply(pd.to_numeric, errors="coerce")
    c1 = ctl[ctl.роль == "C1"].groupby("event_id").id.apply(list)
    rows = []
    for eid, e in ev.iterrows():
        if eid not in X.index: continue
        cs = [c for c in c1.get(eid, []) if c in X.index]
        if len(cs) < 3: continue
        rows.append((eid, cs[:3], e))
    n = len(rows); print(f"{'ПРОБА (разметки нет, не результат)' if probe else 'подтверждённые'}: событий с 3 C1 = {n}", file=sys.stderr)
    rng = np.random.default_rng(a.seed)
    out = []
    for sg in ("+", "-"):
        idx = [i for i, r in enumerate(rows) if str(r[2]["знак"]) == sg]
        if len(idx) < 4: continue
        ids = [rows[i][0] for i in idx]
        E = X.loc[ids].to_numpy(float)                                   # [n, F]
        C = np.stack([X.loc[rows[i][1]].to_numpy(float) for i in idx])   # [n, 3, F]
        Q = np.concatenate([E[:, None, :], C], axis=1)                   # [n, 4, F]
        Sg = np.zeros(Q.shape, np.int8)
        for k in range(4):
            oth = np.delete(Q, k, axis=1)
            d = Q[:, k, :] - np.median(oth, axis=1)
            Sg[:, k, :] = np.where(np.isnan(d), 0, np.sign(d))
        valid = ~np.isnan(Q).any(axis=1)                                 # [n, F]
        obs = Sg[:, 0, :].astype(float)
        meta = ev.loc[ids]
        clus = meta["кластер_id"].to_numpy(); coin = meta["монета_id"].to_numpy(); bx = (meta["биржа"].to_numpy() == "bybit")
        w = 1.0 / pd.Series(clus).map(pd.Series(clus).value_counts()).to_numpy()
        ts = np.array([rows[i][2]["t_start_ms"] for i in idx]); h1 = ts < HALF
        ch = np.array([zlib.crc32(str(c).encode()) & 1 for c in coin]).astype(bool)
        W = w[:, None] * valid                                           # [n, F]
        den = W.sum(0)
        dl = (W * obs).sum(0) / np.where(den > 0, den, np.nan)
        # перестановки внутри четвёрки: метка события — случайный из 4
        P = a.perm; stat = np.empty((P, len(test_cols)))
        ar = np.arange(len(idx))
        for p in range(P):
            kk = rng.integers(0, 4, len(idx))
            stat[p] = (W * Sg[ar, kk, :]).sum(0)
        stat /= np.where(den > 0, den, np.nan)
        pv = (1 + (np.abs(stat) >= np.abs(dl) - 1e-12).sum(0)) / (P + 1)
        mde = np.nanpercentile(np.abs(stat), 95, axis=0)
        ucoin, cinv = np.unique(coin, return_inverse=True)
        def boot(mask):
            m = mask & True
            A = np.zeros((len(ucoin), len(test_cols))); B = np.zeros_like(A)
            np.add.at(A, cinv[m], (W * obs)[m]); np.add.at(B, cinv[m], W[m])
            act = B.sum(1) > 0
            if act.sum() < 2: return None
            cnt = rng.multinomial(act.sum(), np.ones(act.sum()) / act.sum(), size=a.boot)
            num = cnt @ A[act]; dn = cnt @ B[act]
            return num / np.where(dn > 0, dn, np.nan), len(np.unique(clus[m]))
        full, _ = boot(np.ones(len(idx), bool))
        lo, hi = np.nanpercentile(full, [2.5, 97.5], axis=0)
        cuts = {"bybit": bx, "gate": ~bx, "H1": h1, "H2": ~h1, "coinA": ch, "coinB": ~ch}
        share = {}
        for nm, m in cuts.items():
            r = boot(m)
            if r is None or r[1] < 5: share[nm] = np.full(len(test_cols), np.nan); continue
            share[nm] = (np.sign(r[0]) == np.sign(dl)).mean(0)
        ncl_b = len(np.unique(clus[bx])); ncl_g = len(np.unique(clus[~bx]))
        for j, c in enumerate(test_cols):
            if not den[j] > 0 or ncl_b < 2 or ncl_g < 2 or np.unique(clus[valid[:, j] & bx]).size < 2 or np.unique(clus[valid[:, j] & ~bx]).size < 2:
                continue
            out.append(dict(признак=c, фаза=c.rsplit("_", 1)[1], знак=sg, N=int(valid[:, j].sum()), кластеров=int(np.unique(clus[valid[:, j]]).size),
                            delta=dl[j], lo=lo[j], hi=hi[j], p=pv[j], mde=mde[j], **{f"доля_{k}": share[k][j] for k in cuts}))
    R = pd.DataFrame(out)
    R["q"] = bh(R.p.to_numpy(), a.q)
    cutn = ["bybit", "gate", "H1", "H2", "coinA", "coinB"]
    sh = R[[f"доля_{k}" for k in cutn]]
    ok_cut = (sh >= 0.9).all(axis=1)
    R["класс"] = np.where((R.q <= a.q) & ok_cut & (R.delta.abs() >= R.mde), "общее",
                  np.where(R.q <= a.q, "различает, но не везде", np.where((R.lo < 0) & (R.hi > 0) & (R.hi - R.lo < 2 * R.mde), "не отличается в пределах MDE", "не ясно")))
    R.to_csv(a.out, index=False)
    print(f"M={len(R)} ячеек; по-определению вне M: {len(by_def)} колонок; классы:\n{R['класс'].value_counts().to_string()}", file=sys.stderr)


if __name__ == "__main__":
    main()
