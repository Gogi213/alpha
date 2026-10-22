#!/usr/bin/env python3
"""TK-356 (G-015): зависимость дохода от суточного оборота монеты и от шага цены (bps). Описание на обучающем окне, не правило.
Использование: tk356-analyze.py TURNOVER.csv OUT.md OUT.csv [TURNOVER-P24.csv P24-TRADES.csv]   (признаки сделок — docs/findings/*features*;
P24-TRADES.csv: symbol,day_utc,form,entry_px,net_bps,reason — pct0.1/pct0.5 из rounds.csv TK-344)"""
import sys, numpy as np, pandas as pd
F = "docs/findings/"
tv = pd.read_csv(sys.argv[1]); tv = tv[tv.err.isna()]
fut = pd.concat([pd.read_csv(F + "tk327-features-fut-2026-10-20.csv"), pd.read_csv(F + "p23-fut-B-features-2026-10-20.csv")])
spot = pd.read_csv(F + "tk327-features-spot-2026-10-20.csv")
d = pd.concat([fut, spot])
if len(sys.argv) > 5:   # П-24: два набора (pct0.1, pct0.5) как отдельные «режимы», комиссии fut
    tp24 = pd.read_csv(sys.argv[4]); tp24 = tp24[tp24.err.isna()]; tv = pd.concat([tv, tp24])
    t = pd.read_csv(sys.argv[5]).rename(columns={"symbol": "sym", "day_utc": "day"})
    t["mon"] = t.day.str[5:7].astype(int); t["reason"] = t.reason.replace({"early": "deadline"})  # early = выход taker
    for f in ("pct0.1", "pct0.5"):
        x = t[t.form.str.startswith(f + "-")].copy(); x["mode"] = "p24-" + f; d = pd.concat([d, x[["mode", "sym", "day", "mon", "entry_px", "net_bps", "reason"]]])
    tv = tv.drop_duplicates(["mode", "sym", "day"])
    p24 = tv[tv["mode"] == "fut"].copy()
    for f in ("p24-pct0.1", "p24-pct0.5"): p = p24.copy(); p["mode"] = f; tv = pd.concat([tv, p])
d = d.merge(tv[["mode", "sym", "day", "turn_usd", "tick_bin"]], on=["mode", "sym", "day"], how="left")
d["tick_bps"] = d.tick_bin / d.entry_px * 1e4
# комиссии: вход maker; выход taker при stop/deadline (как В-47/модель), иначе maker; ×(1−ребейт 10 %)
mk, tk = {"fut": 1.4, "spot": 6.25, "p24-pct0.1": 1.4, "p24-pct0.5": 1.4}, {"fut": 3.5, "spot": 7.5, "p24-pct0.1": 3.5, "p24-pct0.5": 3.5}
mkf = d["mode"].map(mk) * 0.9; tkf = d["mode"].map(tk) * 0.9
d["fee"] = mkf + np.where(d.reason.isin(["stop", "deadline"]), tkf, mkf)
d["gross"] = d.net_bps + d.fee
d["usd"] = d.net_bps * 500 / 1e4
d["is_stop"] = (d.reason == "stop").astype(float)
rng = np.random.default_rng(356)

def boot(g):   # бутстрап по суткам: среднее net по сделкам, ресэмпл суток
    days = g.groupby("day").net_bps.agg(["sum", "count"]).values
    if len(days) < 3: return np.nan, np.nan
    idx = rng.integers(0, len(days), (2000, len(days)))
    m = days[idx, 0].sum(1) / days[idx, 1].sum(1)
    return np.percentile(m, 2.5), np.percentile(m, 97.5)

def block_tests(g, col, n=5000):
    """Блочные тесты (признак — свойство монеты/суток): (а) по монетам: Спирмен средних по монете, перестановка монет;
    (б) по суткам: кластерный бутстрап суток для Спирмена по сделкам, p = 2·min(доля>0, доля<0)."""
    c = g.groupby("sym").agg(x=(col, "mean"), y=("net_bps", "mean"))
    rc = c.x.rank().corr(c.y.rank()); yy = c.y.rank().values; xx = c.x.rank().values
    pc = float(np.mean([abs(np.corrcoef(xx, rng.permutation(yy))[0, 1]) >= abs(rc) for _ in range(n)])) if len(c) > 3 else np.nan
    days = [grp for _, grp in g.groupby("day")]; bs = []
    for _ in range(1000):
        b = pd.concat([days[i] for i in rng.integers(0, len(days), len(days))])
        bs.append(b[col].rank().corr(b.net_bps.rank()))
    bs = np.array(bs); pdv = float(min(1, 2 * min((bs > 0).mean(), (bs < 0).mean())))
    return rc, pc, len(c), pdv, np.percentile(bs, 2.5), np.percentile(bs, 97.5)

def trend(g, col):   # наклон net_bps на ранг признака (Спирмен) + перестановочный p
    x = g[col].rank(); y = g.net_bps.rank()
    r = np.corrcoef(x, y)[0, 1]
    perm = [np.corrcoef(x, y.sample(frac=1, random_state=i).values)[0, 1] for i in range(500)]
    return r, float(np.mean(np.abs(perm) >= abs(r)))

tp = []; rows = []; out = ["# TK-356 (G-015): доход против суточного оборота и шага цены\n",
    "Срезы — описание на обучающем окне (янв–сен 2026), не правило. Сделки готовые (В-213); gross = net + комиссии "
    "(вход maker; выход taker при stop/deadline, иначе maker; спот — тариф В-47-типа 6,25/7,5 бп × 0,9 — приближение, fut 1,4/3,5 × 0,9).\n"]
for mode in [x for x in ("fut", "spot", "p24-pct0.1", "p24-pct0.5") if (d["mode"] == x).any()]:
    m = d[d["mode"] == mode]
    out.append(f"\n## {mode}: n={len(m)}, без оборота: {int(m.turn_usd.isna().sum())}, средний net {m.net_bps.mean():.2f} бп\n")
    m = m.dropna(subset=["turn_usd", "tick_bps"])
    for col, name in (("turn_usd", "оборот $/сутки"), ("tick_bps", "шаг цены бп")):
        m = m.copy(); m["q"] = pd.qcut(m[col].rank(method="first"), 5, labels=[f"Q{i}" for i in range(1, 6)])
        r, p = trend(m, col); rc, pc, nc, pdv, dlo, dhi = block_tests(m, col); tp.append((mode, col, rc, pc))
        out.append(f"\nБлочные тесты ({mode}, {col}): по монетам (n={nc}) ρ = {rc:+.3f}, p = {pc:.4f}; по суткам ρ сделок {r:+.3f} "
                   f"[{dlo:+.3f}; {dhi:+.3f}], p = {pdv:.3f}. (Перестановка по сделкам p = {p:.3f} — не используется, занижает p.)")
        out.append(f"\n### {mode} · {name}: Спирмен(net,{col}) = {r:+.3f}, перестановочный p = {p:.3f}\n")
        out.append("| корзина | диапазон | n | net бп | 95% (сутки) | gross бп | доля стопов | Σ$ | месяцев + / всего |")
        out.append("|---|---|---|---|---|---|---|---|---|")
        for q, g in m.groupby("q", observed=True):
            lo, hi = boot(g); mon = g.groupby("mon").net_bps.mean()
            out.append(f"| {q} | {g[col].min():.3g}–{g[col].max():.3g} | {len(g)} | {g.net_bps.mean():+.2f} | {lo:+.1f}…{hi:+.1f} | "
                       f"{g.gross.mean():+.2f} | {g.is_stop.mean():.2f} | {g.usd.sum():+.0f} | {(mon > 0).sum()}/{len(mon)} |")
            rows.append(dict(mode=mode, feature=col, bucket=q, lo=g[col].min(), hi=g[col].max(), n=len(g), net=g.net_bps.mean(),
                             ci_lo=lo, ci_hi=hi, gross=g.gross.mean(), stop_share=g.is_stop.mean(), usd=g.usd.sum(),
                             months_pos=int((mon > 0).sum()), months=len(mon)))
        pv = m.pivot_table(index="q", columns="mon", values="net_bps", aggfunc="mean", observed=True).round(1)
        out.append("\nпо месяцам (средний net бп):\n\n" + pv.to_markdown())
    out.append(f"\n### {mode} · шаг: монеты по корзинам\n\n| корзина | монет | крупнейшая (по n) | её доля n | net бп | net без неё | Σ$ без неё |\n|---|---|---|---|---|---|---|")
    for q, g in m.groupby("q", observed=True):
        top = g.groupby("sym").size().idxmax(); gg = g[g.sym != top]
        out.append(f"| {q} | {g.sym.nunique()} | {top} | {(g.sym == top).mean():.2f} | {g.net_bps.mean():+.2f} | {gg.net_bps.mean():+.2f} | {gg.usd.sum():+.0f} |")
    out.append(f"\nСпирмен(оборот, шаг) = {m.turn_usd.rank().corr(m.tick_bps.rank()):+.2f} (признаки связаны: мелкий шаг ↔ крупный оборот).")
o = sorted(range(len(tp)), key=lambda i: tp[i][3]); hp = {}; run = 0.0
for k, i in enumerate(o):
    run = max(run, min(1, tp[i][3] * (len(tp) - k))); hp[i] = run
out.append("\n## Поправка Holm по p блочного теста по монетам (%d теста)\n\n| набор | признак | Спирмен по монетам | p | p Holm |\n|---|---|---|---|---|" % len(tp))
for i, t in enumerate(tp): out.append(f"| {t[0]} | {t[1]} | {t[2]:+.3f} | {t[3]:.3f} | {hp[i]:.3f} |")
open(sys.argv[2], "w", encoding="utf-8").write("\n".join(out) + "\n")
pd.DataFrame(rows).to_csv(sys.argv[3], index=False)
print("\n".join(out))
