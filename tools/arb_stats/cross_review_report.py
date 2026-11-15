"""Сводка cross_review.py: $/сут по настройкам (тейкер-тейкер, без BingX) — всего / с пометками загрязнения / чистые, незакрытые по цене конца окна,
пиковый номинал, доля топ-10 пар, раздельные задержки. Запуск: python3 cross_review_report.py <taint.pkl> > <выход.md>"""
import pickle
import sys

import numpy as np

d = pickle.load(open(sys.argv[1], "rb"))
t0, t1, rows, F = d["t0"], d["t1"], d["rows"], d["F"]
c = {k: i for i, k in enumerate(F)}
win_d = (t1 - t0) / 86_400_000
edges = d["days"]
cfgs = sorted({(k[3], k[4]) for k in rows})


def usd(a):
    m = a[:, c["closed"]] > 0
    return float((a[m, c["size"]] * a[m, c["net"]] / 1e4).sum() / win_d)


print("| θ, % | выход | $/сут всего | из них audit | из них Binance 18:12 | возраст ноги > 60 с (вход или выход) | любая пометка | $/сут чистые | чистые по суткам | "
      "незакрыто, кругов | незакрытые по цене t1, $/сут | итого с незакрытыми, $/сут | пик Σ номинала, $ | средний номинал, $ | топ-10 пар, доля $ | кругов закрыто |")
print("|" + "---|" * 16)
for th, ex in cfgs:
    parts = [v for k, v in rows.items() if k[3] == th and k[4] == ex and len(v)]
    if not parts:
        continue
    a = np.concatenate(parts)
    cl = a[:, c["closed"]] > 0
    aud = a[:, c["audit"]] > 0
    out = a[:, c["out"]] > 0
    old = (a[:, c["age_in"]] > 60_000) | (np.nan_to_num(a[:, c["age_out"]]) > 60_000)
    anyf = aud | out | old
    tot = usd(a)
    clean = a[~anyf]
    perday = []
    for lo, hi in zip(edges[:-1], edges[1:]):
        m = (clean[:, c["ts"]] >= lo) & (clean[:, c["ts"]] < hi) & (clean[:, c["closed"]] > 0)
        perday.append(float((clean[m, c["size"]] * clean[m, c["net"]] / 1e4).sum() / ((hi - lo) / 86_400_000)))
    unc = a[~cl]
    mtm = float((unc[:, c["size"]] * unc[:, c["mtm"]] / 1e4).sum() / win_d)
    ev = np.r_[a[:, c["ts"]], a[:, c["te"]]]
    w = np.r_[a[:, c["size"]], -a[:, c["size"]]]
    o = np.lexsort((w, ev))           # при равном времени сначала выход
    peak = float(np.cumsum(w[o]).max())
    mean = float((a[:, c["size"]] * (a[:, c["te"]] - a[:, c["ts"]])).sum() / (t1 - t0))
    pu = sorted((usd(v) for k, v in rows.items() if k[3] == th and k[4] == ex and len(v)), reverse=True)
    top = sum(pu[:10]) / sum(pu) if sum(pu) else float("nan")
    print(f"| {th / 100:g} | {ex} | {tot:.0f} | {usd(a[aud]):.0f} | {usd(a[out]):.0f} | {usd(a[old]):.0f} | {usd(a[anyf]):.0f} | {usd(clean):.0f} | "
          f"{' / '.join(f'{x:.0f}' for x in perday)} | {int((~cl).sum())} | {mtm:.0f} | {tot + mtm:.0f} | {peak:.0f} | {mean:.0f} | {top:.2f} | {int(cl.sum())} |")

print()
print("Возраст самой старой ноги на входе, доля $/сут (закрытые, все настройки L = 0, θ ≤ 0,3 %):")
for th, ex in cfgs:
    if "@" in ex or th > 30:
        continue
    a = np.concatenate([v for k, v in rows.items() if k[3] == th and k[4] == ex and len(v)])
    a = a[a[:, c["closed"]] > 0]
    u = a[:, c["size"]] * a[:, c["net"]] / 1e4
    ag = a[:, c["age_in"]]
    s = u.sum()
    print(f"- θ {th / 100:g} %, {ex}: " + ", ".join(f"> {x / 1000:g} с — {u[ag > x].sum() / s * 100:.1f} % $ ({(ag > x).mean() * 100:.1f} % кругов)"
                                                   for x in (1000, 10_000, 60_000, 300_000)))
