#!/usr/bin/env python3
"""TK-083: сводка 7 клеток П-12 (после В-201) по месяцам: сырые bps, парная Δ к B1 по суткам, блочный бутстреп.
Вход data/tk083/rows.csv (tk083-collect.py). Выход docs/findings/p12-ext-monthly-2026-10-08.csv + печать."""
import math, sys
import numpy as np, pandas as pd

B1 = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
CELLS = {
    "x-stop-pct2.5": "ladder3x0..0.0409sw2-pct2.5-tr1x1-14400-ttl1800",
    "x-dl-21600": "ladder3x0..0.0409sw2-pct2-tr1x1-21600-ttl1800",
    "x-dl-28800": "ladder3x0..0.0409sw2-pct2-tr1x1-28800-ttl1800",
    "x-entry-n.1-t.5": "ladder3x0.00409..0.02045sw2-pct2-tr1x1-14400-ttl1800",
    "x-entry-n.1-t2": "ladder3x0.00409..0.0818sw2-pct2-tr1x1-14400-ttl1800",
    "x-entry-n.25-t.5": "ladder3x0.010225..0.02045sw2-pct2-tr1x1-14400-ttl1800",
    "x-entry-n.25-t2": "ladder3x0.010225..0.0818sw2-pct2-tr1x1-14400-ttl1800",
}
d = pd.read_csv("data/tk083/rows.csv")
b1 = pd.concat([d[d.src == "b1"], d[d.src == "b1sup"]]).drop_duplicates(["day", "symbol"], keep="last")
b1 = b1.assign(cell="B1")
p = d[d.src == "p12"]
inv = {v: k for k, v in CELLS.items()}
p = p[p.form.isin(inv)].assign(cell=lambda x: x.form.map(inv))
keys = set(zip(p.day, p.symbol))
b1all = b1
b1 = b1[[k in keys for k in zip(b1.day, b1.symbol)]]
miss = b1all[[k not in keys for k in zip(b1all.day, b1all.symbol)]]
print(f"B1 строк {len(b1all)}, в p12 нет {len(miss)} (сигналов {miss.n_signals.sum()}, символов {miss.symbol.nunique()}, суток {miss.day.nunique()}, bps {miss.sum_net_bps.sum():.0f})")
a = pd.concat([b1, p])
a["month"] = a.day.str[:7]
cols = ["n_signals", "n_fills", "sum_net_bps", "n_stop", "n_take", "n_deadline"]
mon = a.groupby(["month", "cell"])[cols].sum().reset_index()
day = a.groupby(["day", "cell"]).sum_net_bps.sum().unstack("cell").fillna(0.0)
days_all = sorted(day.index)
rng = np.random.default_rng(83)


def boot(x, B=20000):
    n = len(x)
    L = max(1, math.ceil(n ** (1 / 3)))
    nb = math.ceil(n / L)
    xs = np.asarray(x)
    st = rng.integers(0, n - L + 1, size=(B, nb))
    idx = (st[:, :, None] + np.arange(L)).reshape(B, -1)[:, :n]
    m = xs[idx].mean(1)
    return xs.mean(), np.percentile(m, [2.5, 97.5]), L


rows = []
for mth in sorted(a.month.unique()) + ["ALL"]:
    sel = [x for x in days_all if mth == "ALL" or x.startswith(mth)]
    base = mon[(mon.cell == "B1") & ((mon.month == mth) if mth != "ALL" else True)][cols].sum()
    for c in ["B1"] + list(CELLS):
        m = mon[(mon.cell == c) & ((mon.month == mth) if mth != "ALL" else True)][cols].sum()
        r = dict(month=mth, cell=c, n_signals=int(m.n_signals), n_fills=int(m.n_fills), sum_net_bps=round(m.sum_net_bps, 1),
                 n_stop=int(m.n_stop), n_take=int(m.n_take), n_deadline=int(m.n_deadline), n_days=len(sel))
        if c != "B1":
            dl = (day.loc[sel, c] - day.loc[sel, "B1"]).values
            mean, ci, L = boot(dl)
            r.update(d_bps=round(m.sum_net_bps - base.sum_net_bps, 1), d_mean_day=round(mean, 2), ci_lo=round(ci[0], 2), ci_hi=round(ci[1], 2))
        rows.append(r)
res = pd.DataFrame(rows)
res.to_csv("docs/findings/p12-ext-monthly-2026-10-08.csv", index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
print(res[res.month == "ALL"].to_string(index=False))
print(res.pivot(index="month", columns="cell", values="d_bps").to_string())
print(res.pivot(index="month", columns="cell", values="sum_net_bps")[["B1"] + list(CELLS)].to_string())
