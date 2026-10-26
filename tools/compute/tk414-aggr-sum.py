#!/usr/bin/env python3
"""TK-414 п.3: свод доли агрессивных покупок по форме × знак × окно. Использование: tk414-aggr-sum.py <aggr.csv> <legs.csv>"""
import sys
import pandas as pd
a = pd.read_csv(sys.argv[1])
for w, n in (("h1", "1-я половина"), ("h2", "2-я половина"), ("после", "30 мин после пика")):
    pre = w + "_" if w == "h1" or w == "h2" else w + "_"
    b, s = a[pre + "buy_usd"], a[pre + "sell_usd"]
    a["sh_" + w] = b / (b + s)
    a["tot_" + w] = b + s
rows = []
for (f, z), g in a.groupby(["форма", "знак"]):
    r = {"форма": f, "знак": z, "n": len(g)}
    for w in ("h1", "h2", "после"):
        gg = g[g["tot_" + w] > 0]
        r["мед_" + w] = round(gg["sh_" + w].median(), 3)
        r["n_" + w] = len(gg)
        # равный вес инструментов: медиана по инструментам медиан
        r["инстр_" + w] = round(gg.groupby(["биржа", "рынок", "инструмент"])["sh_" + w].median().median(), 3)
    rows.append(r)
o = pd.DataFrame(rows)
print(o.to_markdown(index=False))
g = a.groupby("знак")
print(a.groupby("знак")[["sh_h1", "sh_h2", "sh_после"]].median().round(3).to_markdown())
print("покрытие: ног", len(a), "без сделок в h1/h2/после:", [(a["tot_" + w] == 0).sum() for w in ("h1", "h2", "после")])
