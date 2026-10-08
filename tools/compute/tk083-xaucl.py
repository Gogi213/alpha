#!/usr/bin/env python3
"""TK-083: дополнение к вердикту П-12 — XAU/CL, 7 клеток после В-201 (pct3/pct4 не берём): по месяцам сырые bps и парная Δ к B1
(B1 = p07b-base из b14x TK-084, та же клетка/набор). Блочный бутстреп по суткам (блок ⌈n^1/3⌉, 20 000).
Вход: argv[1] b1x.csv (symbol,day,form,n_sig,n_sub,n_fill,..,sum_net_bps=поле 10), argv[2] p12x.csv; выход argv[3]."""
import math, sys
import numpy as np, pandas as pd

C = ["symbol", "day", "form", "n_signals", "n_submitted", "n_fills", "n_busy", "n_rej", "n_cross", "sum_net_bps", "n_stop", "n_take"]
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
rd = lambda f: pd.read_csv(f, header=None, names=C, usecols=range(12))
b = rd(sys.argv[1]).assign(cell="B1")
p = rd(sys.argv[2])
inv = {v: k for k, v in CELLS.items()}
p = p[p.form.isin(inv)].assign(cell=lambda x: x.form.map(inv))
a = pd.concat([b[b.form == B1], p])
a["month"] = a.day.str[:7]
rng = np.random.default_rng(83)


def boot(x, B=20000):
    n = len(x); L = max(1, math.ceil(n ** (1 / 3))); nb = math.ceil(n / L)
    st = rng.integers(0, n - L + 1, size=(B, nb))
    idx = (st[:, :, None] + np.arange(L)).reshape(B, -1)[:, :n]
    m = x[idx].mean(1)
    return x.mean(), np.percentile(m, [2.5, 97.5])


rows = []
for sym in ["CLUSDT", "XAUUSDT", "ALL"]:
    s = a if sym == "ALL" else a[a.symbol == sym]
    for mth in sorted(s.month.unique()) + ["ALL"]:
        t = s if mth == "ALL" else s[s.month == mth]
        day = t.groupby(["day", "cell"]).sum_net_bps.sum().unstack("cell").fillna(0.0)
        for c in ["B1"] + list(CELLS):
            m = t[t.cell == c]
            r = dict(symbol=sym, month=mth, cell=c, n_days=day.shape[0], n_signals=int(m.n_signals.sum()), n_fills=int(m.n_fills.sum()), sum_net_bps=round(m.sum_net_bps.sum(), 1))
            if c != "B1":
                dl = (day[c] - day["B1"]).values
                mean, ci = boot(dl) if len(dl) > 1 else (dl.mean(), (np.nan, np.nan))
                r.update(d_bps=round(dl.sum(), 1), d_mean_day=round(mean, 2), ci_lo=round(ci[0], 2), ci_hi=round(ci[1], 2))
            rows.append(r)
res = pd.DataFrame(rows)
res.to_csv(sys.argv[3], index=False)
pd.set_option("display.width", 250); pd.set_option("display.max_rows", 500)
print(res[res.month == "ALL"].to_string(index=False))
for sym in ["CLUSDT", "XAUUSDT"]:
    q = res[res.symbol == sym]
    print(sym); print(q.pivot(index="month", columns="cell", values="sum_net_bps")[["B1"] + list(CELLS)].to_string())
    print(q.pivot(index="month", columns="cell", values="n_signals")[["B1"]].T.to_string())
