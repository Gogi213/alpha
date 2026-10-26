"""TK-414 возврат Судьи п.1–2: доли форм по ногам и с равным весом инструментов + чувствительность к T_FAST (p5/p10/p20)
и к определению паузы (θ5 | обратная нога θ5 с ходом >= r × ход текущей ноги). Логика формы = tk414-label.py, пороги Судьи
(39 мин для p10 фиксированы; T_TURN 4,84, откат 1/0,5, пила 4×1,5θ). Использование: tk414-sens.py <episodes.csv> <out.md>"""
import sys
import pandas as pd
src, out = sys.argv[1], sys.argv[2]
d = pd.read_csv(src)
key = ['биржа', 'рынок', 'инструмент']
ok = (d.категория == 'волатильный') & (d.плохой_принт != 'да') & (d.сомнительный != 'да')
liq = d[ok & (d['порог_%'] == 10.48) & (d.мед_оборот_дня_usd >= 1e6)].copy()
fine = d[ok & (d['порог_%'] == 5.0)]
T_TURN, THETA = 4.84, 10.48
fi = {k: g for k, g in fine.groupby(key)}
FORMS = ['прямая', 'ступень', 'лестница', 'пила', 'вынос+возврат', 'каскад']


def label(t_fast, rel):
    rows = []
    for k, g in liq.sort_values('начало_utc').groupby(key):
        g = g.reset_index(drop=True)
        f = fi.get(k)
        mv = g['ход_%'].abs().tolist()
        chain = [i + 1 < len(g) and g.iloc[i + 1].начало_utc == g.iloc[i].пик_utc for i in range(len(g))]
        for i, r in g.iterrows():
            nxt = g.iloc[i + 1] if chain[i] else None
            rn = abs(nxt['ход_%']) / abs(r['ход_%']) if nxt is not None else None
            npz = 0
            if f is not None:
                m = f[(f.знак != r.знак) & (f.начало_utc >= r.начало_utc) & (f.конец_utc <= r.пик_utc) & (f.пик_utc <= r.пик_utc)
                      & (f['ход_%'].abs() >= rel * abs(r['ход_%']))]
                npz = len(m)
            pila = False
            for a in range(max(0, i - 3), i + 1):
                if a + 4 <= len(g) and all(chain[a + j] for j in range(3)) and all(x < 1.5 * THETA for x in mv[a:a + 4]):
                    pila = True
            fast = r.длит_до_пика_мин <= t_fast
            if fast and rn is not None and rn >= 1.0:
                form = 'вынос+возврат'
            elif fast and r.знак == 'дамп' and r.оборот_к_норме >= T_TURN and rn is not None and rn >= 0.5:
                form = 'каскад'
            elif pila:
                form = 'пила'
            elif npz >= 2:
                form = 'лестница'
            elif npz == 1:
                form = 'ступень'
            else:
                form = 'прямая'
            rows.append((k, form, r.знак, abs(r['ход_%']), r.длит_до_пика_мин))
    return pd.DataFrame(rows, columns=['инстр', 'форма', 'знак', 'ход', 'длит'])


def shares(o):
    a = o.форма.value_counts(normalize=True).reindex(FORMS).fillna(0) * 100
    per = o.groupby('инстр').форма.value_counts(normalize=True).unstack().reindex(columns=FORMS).fillna(0)
    return a.round(1), (per.mean() * 100).round(1)


q = liq.длит_до_пика_мин.quantile
base = label(q(0.10), 0.0)
lines = ['# TK-414: возврат слоя 2, п.1–2 — доли форм в двух весах и чувствительность', '',
         f'Ноги {len(base)}, инструментов {base.инстр.nunique()}. Скрипт `tools/compute/tk414-sens.py`. Пороги Судьи (В-53): T_FAST=39 мин (p10), T_TURN=4,84, откат 1/0,5, пауза θ5, пила 4×1,5θ.', '',
         '## 1. Доли форм: по ногам и с равным весом инструментов (база)', '', '| форма | по ногам, % | равный вес инстр., % |', '|---|---|---|']
a, e = shares(base)
for f in FORMS:
    lines.append(f'| {f} | {a[f]} | {e[f]} |')
top = base.groupby('инстр').size().sort_values(ascending=False).head(5)
lines += ['', 'Топ-5 инструментов по числу ног: ' + '; '.join(f'{"/".join(k)} — {n}' for k, n in top.items()) + f' (всего {top.sum()} = {top.sum()/len(base)*100:.0f} % ног).', '',
          '## 2. Чувствительность', '', 'Доли, % по ногам / равный вес инструментов.', '',
          '| вариант | T_FAST мин | ' + ' | '.join(FORMS) + ' |', '|---|---|' + '---|' * len(FORMS)]
variants = [(0.05, 0), (0.10, 0), (0.20, 0), (0.10, 0.10), (0.10, 0.25)]
res = {}
for qq, rel in variants:
    o = base if (qq, rel) == (0.10, 0) else label(q(qq), rel)
    a, e = shares(o)
    res[(qq, rel)] = (a, e)
    nm = f'p{int(qq*100)}, пауза θ5' + (f' и ≥{int(rel*100)} % хода ноги' if rel else '')
    lines.append(f'| {nm} | {q(qq):.0f} | ' + ' | '.join(f'{a[f]} / {e[f]}' for f in FORMS) + ' |')
lines += ['', 'Пауза «≥ r × ход ноги» значима только для ног, длиннее 5/r % (r=0,10 — от 50 %; r=0,25 — от 20 %): θ5-ноги внутри не короче 5 %.', '',
          '## 3. Устойчивые выводы']
rng = {f: (min(res[v][0][f] for v in res), max(res[v][0][f] for v in res)) for f in FORMS}
rge = {f: (min(res[v][1][f] for v in res), max(res[v][1][f] for v in res)) for f in FORMS}
for f in FORMS:
    lines.append(f'- {f}: по ногам {rng[f][0]}–{rng[f][1]} %, равный вес {rge[f][0]}–{rge[f][1]} %')
open(out, 'w', encoding='utf-8', newline='\n').write('\n'.join(lines) + '\n')
print('\n'.join(lines))
