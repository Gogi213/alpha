#!/usr/bin/env python3
"""TK-355 (G-015 E/L/Z): таблица ручек очередь/RTT/размер для B1 fut из g015-elz-rounds.csv (busy-replay без --keep, все монеты, янв–сен).
  g015-elz-table.py <elz-rounds.csv> <out.md> <out.csv>
net_bps — из rounds (тариф (а): вход maker, выход taker при stop/deadline — как tk356-analyze.py: 1,4/3,5 бп × 0,9); gross = net + fee.
Интервал — бутстрап суток (кластер), 2000 ресэмплов, зерно 355. Месяцы отдельно; «пул» — только справка."""
import sys, numpy as np, pandas as pd
d = pd.read_csv(sys.argv[1]); rng = np.random.default_rng(355)
mk, tk = 1.4 * 0.9, 3.5 * 0.9
d["fee"] = mk + np.where(d.reason.isin(["stop", "deadline"]), tk, mk)
d["gross"] = d.net_bps + d.fee
d["usd"] = d.net_bps * d.qty * d.entry_px / 1e4
d["notional"] = d.qty * d.entry_px
d["mon"] = d.day_utc.str[5:7].astype(int)
CFG = [("base", "боевая: prob:3, RTT В-68 (~4–6 мс), $500", "реально"),
       ("qrisk", "очередь risk-adverse (пессимист)", "реально"),
       ("rtt0", "RTT 0", "фантастика"), ("rtt1ms", "RTT 1 мс", "реально-предел"), ("rtt10ms", "RTT 10 мс", "реально"),
       ("rtt100ms", "RTT 100 мс", "реально (медленно)"), ("rtt1000ms", "RTT 1000 мс", "реально (медленно)"),
       ("usd250", "размер $250", "реально"), ("usd5000", "размер $5000 (×10)", "реально-предел"), ("usd50000", "размер $50000 (×100)", "фантастика")]

def ci(g, col="net_bps"):
    days = g.groupby("day_utc")[col].agg(["sum", "count"]).values
    if len(days) < 3: return np.nan, np.nan
    idx = rng.integers(0, len(days), (2000, len(days)))
    m = days[idx, 0].sum(1) / days[idx, 1].sum(1)
    return np.percentile(m, 2.5), np.percentile(m, 97.5)

def stat(g):
    lo, hi = ci(g)
    return dict(n=len(g), n_days=g.day_utc.nunique(), net=g.net_bps.mean(), lo=lo, hi=hi, gross=g.gross.mean(), fee=g.fee.mean(),
                usd=g.usd.sum(), fill=g.fill_frac.mean(), stop=(g.reason == "stop").mean())

rows = []; out = ["# G-015 / TK-355: ручки E (очередь), L (RTT), Z (размер) — B1 фьюч, проход (22.10)\n"]
for c, name, tag in CFG:
    g = d[d.cfg == c]
    for m in range(1, 10): rows.append(dict(cfg=c, name=name, tag=tag, month=m, **stat(g[g.mon == m])))
    rows.append(dict(cfg=c, name=name, tag=tag, month=0, **stat(g)))
t = pd.DataFrame(rows); t.to_csv(sys.argv[3], index=False, float_format="%.3f")
base = t[t.cfg == "base"].set_index("month")
out.append("net — бп на круг после тарифа (а); gross = net + комиссия; интервал 95 % — бутстрап суток; месяц 0 = пул янв–сен (справка, не вывод). "
           "Σ$ = net·qty·px; fill — средняя доля исполнения входа. «+мес» — месяцев с net > 0 из 9.\n")
b0 = d[d.cfg == "base"].set_index(["symbol", "day_utc", "signal_index"])
def paired(c):   # Δnet к base на общих сделках (symbol, сутки, signal_index), бутстрап суток
    x = d[d.cfg == c].set_index(["symbol", "day_utc", "signal_index"]); j = x.net_bps.to_frame("a").join(b0.net_bps.to_frame("b"), how="inner").reset_index()
    j["dn"] = j.a - j.b; lo, hi = ci(j, "dn"); return len(j), j.dn.mean(), lo, hi
out.append("| конфиг | метка | n (пул) | дней | net пул бп [95 %] | gross | комиссия | Σ$ | fill | стоп | +мес | Δ к base парно (общие сделки) [95 %] | Δnet к base по месяцам (янв…сен) |\n|---|---|---|---|---|---|---|---|---|---|---|---|---|")
for c, name, tag in CFG:
    x = t[t.cfg == c].set_index("month"); p = x.loc[0]; pos = int((x.loc[1:9].net > 0).sum())
    dl = " ".join(f"{x.loc[m].net - base.loc[m].net:+.1f}" for m in range(1, 10)) if c != "base" else "—"
    pn, pm, plo, phi = paired(c) if c != "base" else (0, 0, 0, 0)
    pr = f"{pm:+.2f} [{plo:+.2f}; {phi:+.2f}] (n {pn})" if c != "base" else "—"
    out.append(f"| {c}: {name} | {tag} | {int(p.n)} | {int(p.n_days)} | {p.net:+.2f} [{p.lo:+.1f}; {p.hi:+.1f}] | {p.gross:+.2f} | {p.fee:.2f} | {p.usd:+.0f} | {p.fill:.2f} | {p.stop:.2f} | {pos}/9 | {pr} | {dl} |")
out.append("\n## По месяцам: net бп на круг [95 %], n / дней\n")
out.append("| месяц | " + " | ".join(c for c, _, _ in CFG) + " |\n|---|" + "---|" * len(CFG))
for m in range(1, 10):
    out.append(f"| {m:02d} | " + " | ".join(f"{t[(t.cfg==c)&(t.month==m)].net.iloc[0]:+.1f} [{t[(t.cfg==c)&(t.month==m)].lo.iloc[0]:+.0f};{t[(t.cfg==c)&(t.month==m)].hi.iloc[0]:+.0f}] {int(t[(t.cfg==c)&(t.month==m)].n.iloc[0])}/{int(t[(t.cfg==c)&(t.month==m)].n_days.iloc[0])}" for c, _, _ in CFG) + " |")
# совместная строка: RTT 0 + боевой выход + ребейт −2 бп/ногу (post hoc: gross + 4 бп, комиссия = −4 бп на круг)
r0 = d[d.cfg == "rtt0"].copy(); r0["net_j"] = r0.gross + 4.0
out.append("\n## Совместная строка «всё идеально, что измеряется»: RTT 0 + боевой выход + ребейт −2 бп/ногу (арифметика post hoc: gross + 4 бп; фантастика)\n")
out.append("Не измерено: очередь «первые» (оптимист) и оракул-выход в этой строке (кода нет).\n")
out.append("| месяц | n | дней | gross rtt0 | net при ребейте −2/ногу [95 %] | net при комиссии 0 |\n|---|---|---|---|---|---|")
for m in range(1, 10):
    g = r0[r0.mon == m]; lo, hi = ci(g, "net_j")
    out.append(f"| {m:02d} | {len(g)} | {g.day_utc.nunique()} | {g.gross.mean():+.2f} | {g.net_j.mean():+.2f} [{lo:+.1f}; {hi:+.1f}] | {g.gross.mean():+.2f} |")
lo, hi = ci(r0, "net_j")
out.append(f"| пул | {len(r0)} | {r0.day_utc.nunique()} | {r0.gross.mean():+.2f} | {r0.net_j.mean():+.2f} [{lo:+.1f}; {hi:+.1f}] | {r0.gross.mean():+.2f} |")
open(sys.argv[2], "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
