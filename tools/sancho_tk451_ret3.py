"""TK-451 возврат 3: §0 (одна таблица), слой 2 K=216 B=20000. Запуск: python -I tools/sancho_tk451_ret3.py"""
import pandas as pd, shutil
D = 'data/tk451/'; R = 'data/tk451-ret3/'; O = 'docs/findings/sancho-2026-10-30'
f = lambda x, n=1: f'{x:.{n}f}'.replace('.', ',')
shutil.copy(R + 'l2-layer2.csv', O + '-layer2.csv')
l2 = pd.read_csv(R + 'l2-layer2.csv'); ag = pd.read_csv(D + 'l1agg.csv'); v = pd.read_csv(D + 'l1verdict.csv')
y1 = pd.concat([pd.read_csv(D + f'l1years-k{k}.csv').assign(k=k) for k in (1, 2)])
auth = {'A1': ('истинный пробой', 61, None), 'A2': ('пробой <0,2 ATR', 49.2, None), 'A3': ('пробой 0,2–0,5 ATR', 53.9, None),
        'A4': ('пробой >0,5 ATR', 66.5, None), 'A5': ('свип → разворот', 62.2, None), 'A6': ('свип+имбаланс', 84.1, 28.2),
        'A7': ('свип+слом', 89, 28.8), 'A8': ('все три', 91.6, 15), 'A9': ('прокол тенью, мед. ATR', 0.27, None)}
YR = [f'y{y}' for y in range(2020, 2027)]; KEY = ['K_I', 'g', 'K_B', 'sc']
m1 = {'воспроизводится': 'подтверждено', 'не воспроизводится': 'оспорено'}
rank = {'воспроизводится': 3, 'не различимо (остров)': 2, 'не различимо': 2, 'не воспроизводится': 1}
fam = l2[l2.family == True].copy()
fam['grp'] = fam.oc.str.replace(r'(m\d)(lo|hi)$', r'\1', regex=True)
need = lambda h: 2 if h.grp.iloc[0].startswith('x3') else 1


def groups(s):
    return [h for _, h in s.groupby(['claim', 'tf', 'k', 'dir', 'grp'])]


def ok(h):  # X3 подтверждён, только если подтверждают обе границы lo/hi
    return len(h) == need(h) and bool((h.verdict == 'подтверждено').all())


def pick(df, s, c, k, N, oc, r):
    q = df[(df.subset == s) & (df.claim == c) & (df.k == k) & (df.N == N) & (df.oc == oc) & (df.tf == 60)]
    for kk in KEY:
        q = q[q[kk] == r[kk]]
    return q


rows = []; tot = 0
for c, (nm, au, ash) in auth.items():
    oc = 'med' if c == 'A9' else 'x1'; n2 = 2 if c == 'A9' else 1
    g = ag[(ag.subset == 'all84') & (ag.tf == 60) & (ag.k == 2) & (ag.N == 10) & (ag.claim == c) & (ag.oc == oc)]
    if c == 'A9':
        g = ag[(ag.subset == 'all84') & (ag.tf == 60) & (ag.k == 2) & (ag.claim == c) & (ag.oc == oc)]
    r = g.loc[(g.p - au).abs().idxmin()]
    cell = '' if c in ('A1', 'A5', 'A9') else f', K_I={int(r.K_I)}, g={r.g}, K_B={int(r.K_B)}, sc={r.sc}'
    defn = f'k=2, {oc}{cell}' if c == 'A9' else f'k=2, N={int(r.N)}, {oc}{cell}'
    ys = [pick(y1, s, c, 2, r.N, oc, r).iloc[0] for s in YR]
    tol = 0.005 if c == 'A9' else 0.5 * 10 ** -len(str(au).split('.')[-1]) if '.' in str(au) else 0.5
    ny = sum(abs(q.p - au) <= tol + 1e-9 for q in ys)
    gy = y1[(y1.claim == c) & (y1.k == 2)]
    nr = 0
    for sy in YR:
        rr = gy[gy.subset == sy]; nr += int(rr.hit.sum() >= (1 if len(rr) <= 2 else 2))
    t = v[(v.subset == 'all84') & (v.claim == c)]
    v1 = m1.get(t.loc[t.verdict.map(rank).idxmax()].verdict, 'не различимо') if len(t) else 'не различимо'
    sh = ''
    if c in ('A6', 'A7', 'A8'):
        if int(t.hit_and_share.fillna(0).sum()) == 0:
            v1 = 'оспорено как заявлено (процент достижим, доля нет)'
        sh = f'; доля: наша {f(r.share)} % против {f(ash)} %'
        vy = 'оспорено как заявлено (доля)'
    else:
        vy = 'подтверждено' if nr == 7 else 'оспорено' if nr == 0 else 'не различимо'
    ea = pick(y1, 'early', c, 2, r.N, oc, r); la = pick(y1, 'late', c, 2, r.N, oc, r)
    el = f'{f(ea.p.iloc[0], n2)} / {f(la.p.iloc[0], n2)}' if len(ea) and len(la) else '—'
    s2 = fam[fam.claim == c]
    if len(s2):
        gs = groups(s2); nc = sum(ok(h) for h in gs); tot += nc
        v2 = f'подтверждено ({nc} из {len(gs)} строк семейства)' if nc else f'не различимо (0 из {len(gs)})'
    elif c in ('A2', 'A3'):
        v2 = 'вне семейства Холма (протокол) — не различимо'
    else:
        v2 = '—'
    pv = f(r.p, n2) + ('' if c == 'A9' else ' [' + f(r.lo, n2) + '–' + f(r.hi, n2) + ']')
    dol = f' (доля {f(ash)} %)' if ash else ''
    rows.append(f'| {c} | {nm} {f(au, n2)}{dol} | {defn} | {pv}{sh} | {" · ".join(f(q.p, n2) for q in ys)} | {ny}/7 · {nr}/7 | {el} | {v1} | {vy} | {v2} |')
T0 = '\n'.join(rows)
lines = []
for h in groups(fam):
    if ok(h):
        r = h.iloc[0]
        lines.append(f'{r.claim} {"1H" if r.tf == 60 else "4H" if r.tf == 240 else "1D"} k={int(r.k)} {"низ" if r.dir == "L" else "верх"} {r.grp}: Δ к C1 {h.delta_C1.min()*100:+.1f}…{h.delta_C1.max()*100:+.1f} пп, к C2 {h.delta_C2.min()*100:+.1f}…{h.delta_C2.max()*100:+.1f} пп, p Холма ≤ {h.p_holm.max():.4f}, знак «+» в {int(h.yrs_pos.min())}/{int(h.yrs_n.iloc[0])} лет')
nopo = sum((h.verdict == 'оспорено').any() for h in groups(fam))
txt = f'''## 0. Итог для владельца: заявление автора → наше число → вердикт (1H, 84 монеты Binance USDⓈ-M, якорь N=10)

Каждая строка — одно определение (k=2 — 5-свечный свинг автора; N=10 баров «@10» — прочтение, автором не подтверждено; исход x1 «состояние на баре +10»; для A2–A4, A6–A8 — ячейка сетки осей, ближайшая к числу автора). Числа — %, [95 % ДИ по дням]; годы 2020…2026 при том же определении (2026 неполный; N события ≥ 17 тыс. на год). Вердикт из трёх слов: слой 1 (число автора воспроизводится при разумных определениях — правило протокола §4), «по годам» (лет в допуске автора — полушаг его последней цифры: «это определение» — ровно эта ячейка, «область осей» — связная область в допуске в году, как §7.1; 7/7 — подтверждено, 0/7 — оспорено, иначе не различимо; вердикт по области осей), слой 2 (честная проверка против контролей C1/C2, Холм по K=216, B=20 000 — поправка протокола §10 п.1). «Ранние / поздние» — монеты с историей с 2020 против остальных (смещение выжившего списка). Прибыль не оценивается.

| id | заявление автора, % | определение | наше число, % [ДИ] | по годам 2020 · 21 · 22 · 23 · 24 · 25 · 26 | лет в допуске: это определение · область осей | ранние / поздние монеты | слой 1, итог | слой 1, по годам | слой 2 (K=216) |
|---|---|---|---|---|---|---|---|---|---|
{T0}

**Слой 2 при K=216, B=20 000:** подтверждено {tot} строк семейства (X3 — только если подтверждают обе границы lo/hi), остальное — не различимо; строк с «оспорено» в семействе — {nopo}. Заменяет «A5 подтверждено (K=188)» и «0 подтверждённых при K=564» (§8.2): K=564 расширял семейство после исходов, потолок Холма при B=4000 (0,054) был дефектом протокола, исправленным без смены вопроса и метрики. Подтверждённые строки: {'; '.join(lines)}. Таблицы §1, §2, §7.1, §8 ниже — рабочие (приложение); при расхождении действует §0.

'''
p = O + '.md'; s = open(p, encoding='utf-8').read()
if '## 0. Итог для владельца' in s:
    s = s[:s.index('## 0. Итог для владельца')] + s[s.index('## 1. '):]
head, rest = s.split('\n## 1. ', 1)
s = head + '\n' + txt + '## 1. ' + rest
for a, b in [('## 1. Заявление автора', '## 1. [рабочий, итог — §0] Заявление автора'), ('## 2. Слой 2 — честная', '## 2. [рабочий, итог — §0] Слой 2 — честная'),
             ('### 7.1 Сводная', '### 7.1 [рабочий, итог — §0] Сводная'), ('## 8. Возврат 2:', '## 8. [рабочий; K=564 заменён §0] Возврат 2:')]:
    s = s.replace(a, b, 1)
open(p, 'w', encoding='utf-8', newline='\n').write(s)

