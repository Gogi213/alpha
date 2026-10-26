"""TK-414 слой 2: разметка ног зигзага TK-413 в формы (пороги — ПРЕДЛОЖЕНИЕ, утверждает Судья, В-53).
Вход: pump-dump-episodes-sep csv (θ 10,48 и θ 5), ликвидные: мед. оборот дня >= 1 млн $, без плохих/сомнительных.
Выход: csv разметки + сводка в stdout. Использует только данные до конца ноги и следующую ногу (разметка задним числом, не онлайн)."""
import sys
import pandas as pd
src, out = sys.argv[1], sys.argv[2]
d = pd.read_csv(src)
key = ['биржа', 'рынок', 'инструмент']
ok = (d.категория == 'волатильный') & (d.плохой_принт != 'да') & (d.сомнительный != 'да')
liq = d[ok & (d['порог_%'] == 10.48) & (d.мед_оборот_дня_usd >= 1e6)].copy()
fine = d[ok & (d['порог_%'] == 5.0)]
q = liq.длит_до_пика_мин.quantile
# ПРЕДЛОЖЕННЫЕ пороги (из квантилей распределения ликвидных ног; Судья утверждает)
T_FAST = q(0.10)            # быстрая нога: длит. до пика <= p10
T_TURN = liq.оборот_к_норме.quantile(0.75)
R_FULL, R_PART = 1.0, 0.5   # откат следующей ноги / текущей
THETA = 10.48
fi = {k: g for k, g in fine.groupby(key)}
rows = []
for k, g in liq.sort_values('начало_utc').groupby(key):
    g = g.reset_index(drop=True)
    f = fi.get(k)
    sizes = g['ход_%'].abs().tolist()
    for i, r in g.iterrows():
        nxt = g.iloc[i + 1] if i + 1 < len(g) and g.iloc[i + 1].начало_utc == r.пик_utc else None
        rn = abs(nxt['ход_%']) / abs(r['ход_%']) if nxt is not None else None
        # внутренние паузы: ноги θ5 обратного знака внутри ноги
        npz = 0
        if f is not None:
            m = f[(f.знак != r.знак) & (f.начало_utc >= r.начало_utc) & (f.конец_utc <= r.пик_utc) & (f.пик_utc <= r.пик_utc)]
            npz = len(m[(m.откат_доля.notna())])
        pila = False
        for a in range(max(0, i - 3), i + 1):
            w = g.iloc[a:a + 4]
            if len(w) == 4 and all(w.iloc[j + 1].начало_utc == w.iloc[j].пик_utc for j in range(3)) and all(abs(x) < 1.5 * THETA for x in w['ход_%']) and a <= i < a + 4:
                pila = True
        fast = r.длит_до_пика_мин <= T_FAST
        if nxt is None:
            pair = 'не закрыта' if r.завершён != 'да' else 'нет следующей в выборке'
        elif rn >= R_FULL:
            pair = 'полный возврат'
        elif rn >= R_PART:
            pair = 'частичный откат'
        else:
            pair = 'малый откат (тренд)'
        if fast and rn is not None and rn >= R_FULL:
            form = 'вынос+возврат (быстрый)'
        elif fast and r.знак == 'дамп' and r.оборот_к_норме >= T_TURN and rn is not None and rn >= R_PART:
            form = 'каскад (по ценам и обороту)'
        elif pila:
            form = 'пила'
        elif npz >= 2:
            form = 'лестница'
        elif npz == 1:
            form = 'ступень'
        else:
            form = 'прямая'
        rows.append(dict(биржа=k[0], рынок=k[1], инструмент=k[2], знак=r.знак, начало_utc=r.начало_utc, пик_utc=r.пик_utc,
                         ход_pct=r['ход_%'], длит_мин=r.длит_до_пика_мин, оборот_к_норме=r.оборот_к_норме,
                         откат_след=None if rn is None else round(rn, 3), пауз_внутри=npz, быстрая=fast,
                         пара=pair, форма=form, синхрон=r.синхрон))
o = pd.DataFrame(rows)
o.to_csv(out, index=False, encoding='utf-8')
print(f'ног {len(o)}, инстр. {o.groupby(key[:3]).ngroups}; T_FAST={T_FAST:.0f} мин, T_TURN={T_TURN:.2f}')
for c in ('форма', 'пара'):
    print(o.groupby(['знак', c]).size().unstack(0).assign(доля=lambda t: (t.sum(axis=1) / len(o) * 100).round(1)).to_string())
print(o.groupby('форма').agg(n=('ход_pct', 'size'), ход_мед=('ход_pct', lambda s: s.abs().median()), длит_мед=('длит_мин', 'median'), обор_мед=('оборот_к_норме', 'median'), синхрон_да=('синхрон', lambda s: (s == 'да').mean())).round(2).to_string())
