#!/usr/bin/env python3
"""TK-461 D2a: сводная таблица отчёта из cells/years csv (правки Судьи 30.10). python tk461_d2a_report.py <cells.csv> <years.csv>"""
import sys
import pandas as pd

K = ["tf", "ev", "dir", "stop", "take", "par"]
c = pd.read_csv(sys.argv[1])
y = pd.read_csv(sys.argv[2])
y = y[y.n >= 8]
yk = y.groupby(K).delta.min().rename("ymin").reset_index()
c = c.merge(yk, on=K, how="left")
c["h_pos"] = (c.p_holm < 0.05) & (c.delta > 0)
c["h_neg"] = (c.p_holm < 0.05) & (c.delta < 0)
c["h_pos_yr"] = c.h_pos & (c.ymin > 0)
g = c.groupby(["tf", "side"])
t = pd.DataFrame({"ячеек": g.size(), "base_плюс": g.apply(lambda d: (d.base > 0).sum()), "delta_плюс": g.apply(lambda d: (d.delta > 0).sum()),
                  "нижняя_гр_плюс": g.apply(lambda d: (d.lo > 0).sum()), "Холм_Δ>0": g.h_pos.sum(), "Холм_Δ<0": g.h_neg.sum(),
                  "Холм_Δ>0_все_годы": g.h_pos_yr.sum(), "лучш_base": g.base.max().round(1), "лучш_delta": g.delta.max().round(1)})
t.loc["всего"] = [len(c), (c.base > 0).sum(), (c.delta > 0).sum(), (c.lo > 0).sum(), c.h_pos.sum(), c.h_neg.sum(), c.h_pos_yr.sum(),
                  round(c.base.max(), 1), round(c.delta.max(), 1)]
print(t.to_markdown())
