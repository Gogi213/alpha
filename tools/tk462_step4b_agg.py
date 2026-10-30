#!/usr/bin/env python3
"""TK-462 шаг 4б: сводка X1/X2. python tk462_step4b_agg.py <каталог s4b> <out.csv>
X2: на сделках, исполненных при всех задержках 0/1/2, средний net (В-63, фандинг по факту) и сдвиг net(delay)-net(0), бутстрап по монетам (B=4000, seed 462).
X1: доля сделок, где в минуте выхода затронуты обе границы (симулятор берёт стоп) и верхняя оценка смещения = доля * (средний gross целей + средний риск)."""
import glob, sys
import numpy as np, pandas as pd
d = pd.concat([pd.read_csv(p) for p in glob.glob(sys.argv[1] + "/*-*.csv.gz")], ignore_index=True)
f = d[d.gross.notna()].copy(); f["net"] = f.gross - f.fee - f.fund
rng = np.random.default_rng(462); rows = []
for tf, g in list(f.groupby("tf")) + [("all", f)]:
    w = g.pivot_table(index=["sym", "t0"], columns="delay", values="net", aggfunc="first").dropna()
    coin = w.index.get_level_values(0).values
    uc = np.unique(coin); idx = {c: np.where(coin == c)[0] for c in uc}
    for dl in (1, 2):
        x = (w[dl] - w[0]).values
        sc = np.array([x[idx[c]].sum() for c in uc]); nc = np.array([len(idx[c]) for c in uc], float)
        bs = [(sc @ m) / (nc @ m) for m in rng.multinomial(len(uc), np.ones(len(uc)) / len(uc), size=4000).astype(float)]
        rows.append(dict(tf=tf, test=f"X2 задержка +{dl} мин", n=len(w), mean_net0=round(w[0].mean(), 3), mean_netD=round(w[dl].mean(), 3),
                         shift=round(x.mean(), 4), lo=round(np.quantile(bs, .025), 4), hi=round(np.quantile(bs, .975), 4)))
    g0 = g[g.delay == 0]; sh = g0.both.mean()
    tg = g0[g0.reason == "target"].gross.mean(); rk = g0.risk.mean()
    rows.append(dict(tf=tf, test="X1 обе границы в минуте выхода", n=len(g0), share_both=round(sh, 5), mean_target_gross=round(tg, 2), mean_risk=round(rk, 2),
                     ub_bias_bps=round(sh * (tg + rk), 3)))
r = pd.DataFrame(rows); r.to_csv(sys.argv[2], index=False); print(r.to_string())
