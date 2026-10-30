#!/usr/bin/env python3
"""TK-461 D2b: сводная таблица и лучшие 10 из cells/years csv. python tk461_d2b_table.py <cells.csv> <years.csv>"""
import sys
import pandas as pd

K = ["tf", "ev", "dir", "stop", "take", "par", "be_r", "ot", "otp"]
c = pd.read_csv(sys.argv[1])
y = pd.read_csv(sys.argv[2])
y = y[y.n >= 8]
for d in (c, y):
    for k in ("par", "be_r", "ot", "otp"):
        d[k] = d[k].astype(str)
yk = y.groupby(K).delta.min().rename("ymin").reset_index()
c = c.merge(yk, on=K, how="left")
c["h_pos"] = (c.p_holm < 0.05) & (c.delta > 0)
c["h_neg"] = (c.p_holm < 0.05) & (c.delta < 0)
c["h_pos_yr"] = c.h_pos & (c.ymin > 0)
c["h_pos_yr_base"] = c.h_pos_yr & (c.base > 0)
g = c.groupby(["tf", "side"])
t = pd.DataFrame({"ячеек": g.size(), "base_плюс": g.apply(lambda d: (d.base > 0).sum()), "delta_плюс": g.apply(lambda d: (d.delta > 0).sum()),
                  "Холм_Δ>0": g.h_pos.sum(), "Холм_Δ<0": g.h_neg.sum(), "Холм_Δ>0_все_годы": g.h_pos_yr.sum(),
                  "из них base>0": g.h_pos_yr_base.sum()})
t.loc["всего"] = [len(c), (c.base > 0).sum(), (c.delta > 0).sum(), c.h_pos.sum(), c.h_neg.sum(), c.h_pos_yr.sum(), c.h_pos_yr_base.sum()]
print(t.to_markdown())
print()
top = c[c.h_pos_yr_base].sort_values("base", ascending=False).head(10)
print(top[K + ["side", "n", "syms", "base", "ctrl", "delta", "lo", "hi", "ymin", "p_holm"]].round(3).to_markdown(index=False))
