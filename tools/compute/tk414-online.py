"""TK-414 итерация 1б: время опознания формы онлайн. Для ног θ10,48 (ликвидные, как в tk414-label.py) — момент 1-й/2-й паузы
(обратная нога θ5 внутри ноги): начало паузы (нижняя граница — известно лишь после того, как пауза началась) и её пик
(верхняя — пауза подтверждена разворотом). Доля хода/времени ноги, уже пройденная к этому моменту. Вход: episodes csv, legs csv."""
import sys
import pandas as pd
ep, legs, out = sys.argv[1:4]
d = pd.read_csv(ep)
L = pd.read_csv(legs)
fine = d[(d['порог_%'] == 5.0) & (d.категория == 'волатильный') & (d.плохой_принт != 'да') & (d.сомнительный != 'да')]
fi = {k: g for k, g in fine.groupby(['биржа', 'рынок', 'инструмент'])}
big = d[d['порог_%'] == 10.48].set_index(['биржа', 'рынок', 'инструмент', 'знак', 'начало_utc'])
rows = []
for _, r in L.iterrows():
    k = (r.биржа, r.рынок, r.инструмент)
    f = fi.get(k)
    b = big.loc[k + (r.знак, r.начало_utc)]
    p0, p1 = b.цена_начала, b.цена_пика
    t0, t1 = pd.Timestamp(r.начало_utc), pd.Timestamp(r.пик_utc)
    dur = (t1 - t0).total_seconds() / 60
    ps = []
    if f is not None:
        m = f[(f.знак != r.знак) & (f.начало_utc >= r.начало_utc) & (f.пик_utc <= r.пик_utc) & f.откат_доля.notna()].sort_values('начало_утс' if False else 'начало_utc')
        for _, x in m.iterrows():
            ts, te = pd.Timestamp(x.начало_utc), pd.Timestamp(x.пик_utc)
            mv = (x.цена_начала / p0 - 1) / (p1 / p0 - 1)
            ps.append((ts, te, mv))
    row = dict(форма=r.форма, знак=r.знак, длит=dur, пауз=len(ps))
    for i in (1, 2):
        if len(ps) >= i:
            ts, te, mv = ps[i - 1]
            row[f'п{i}_t_нач'] = (ts - t0).total_seconds() / 60 / max(dur, 1e-9)
            row[f'п{i}_t_пик'] = (te - t0).total_seconds() / 60 / max(dur, 1e-9)
            row[f'п{i}_ход_нач'] = mv
            row[f'п{i}_мин_нач'] = (ts - t0).total_seconds() / 60
            row[f'п{i}_мин_пик'] = (te - t0).total_seconds() / 60
    rows.append(row)
o = pd.DataFrame(rows)
o.to_csv(out, index=False)
q = lambda s: s.quantile([.25, .5, .75]).round(2).tolist()
for form, g in o.groupby('форма'):
    print(f'\n{form} n={len(g)} мед.длит={g.длит.median():.0f} мин')
    for i in (1, 2):
        c = f'п{i}_t_нач'
        if c in g and g[c].notna().sum():
            h = g[g[c].notna()]
            print(f'  пауза{i}: n={len(h)}; доля времени ноги к началу паузы {q(h[c])}; к пику паузы {q(h[f"п{i}_t_пик"])}; доля хода к началу {q(h[f"п{i}_ход_нач"])}; мин от старта (нач) {q(h[f"п{i}_мин_нач"])}')
