"""TK-452: аудит Excel Санчо по КАЖДОЙ ячейке. Пишет csv/json/log в docs/findings, печатает сводку. Файл не меняет."""
import openpyxl, csv, math, re, json, statistics
from collections import Counter
from scipy.stats import beta
P = r'C:/Users/Георгий/Downloads/Статистика бэктеста — 88 активов.xlsx'
D = '2026-10-30'
OUT = f'docs/findings/sancho-xlsx-{D}'
f = openpyxl.load_workbook(P)
v = openpyxl.load_workbook(P, data_only=True)
L = []


def say(*a):
    s = ' '.join(str(x) for x in a)
    L.append(s)
    print(s)


for ws in f:
    cm = [(c.coordinate, c.comment.text) for r in ws.iter_rows() for c in r if c.comment]
    say('META', ws.title, ws.sheet_state, 'comments', cm,
        'hidden cols', [k for k, d in ws.column_dimensions.items() if d.hidden],
        'hidden rows', [k for k, d in ws.row_dimensions.items() if d.hidden])
say('META names', list(f.defined_names.keys()))
for k in ('B13', 'C13', 'D13'):
    af = f['Сводка'][k].value
    say('Сводка', k, 'ArrayFormula', af.ref, af.text, 'cached', v['Сводка'][k].value)

SH = {'1H': ('1H · 88 активов', 3, 90), '4H': ('4H · по активам', 3, 73), '1D': ('1D · по активам', 3, 52)}
COLS = ['touch', 'brk', 'brk_t', 'brk_p', 'b02', 'b02_t', 'b02_p', 'b0205', 'b0205_t', 'b0205_p', 'b05', 'b05_t', 'b05_p',
        'sw', 'sw_r', 'sw_p', 'im', 'im_r', 'im_p', 'br', 'br_r', 'br_p', 'fs', 'fs_r', 'fs_p']
colL = {k: openpyxl.utils.get_column_letter(i + 2) for i, k in enumerate(COLS)}
data = {}
tot = {}
for tf, (n, a, b) in SH.items():
    rows = []
    for r in range(a, b + 1):
        vals = [v[n].cell(r, c).value for c in range(1, 27)]
        rows.append((r, vals[0], dict(zip(COLS, vals[1:]))))
    data[tf] = rows
    tv = [v[n].cell(b + 1, c).value for c in range(1, 27)]
    assert tv[0] == 'ИТОГО'
    tot[tf] = dict(zip(COLS, tv[1:]))

cs = []


def bad(kind, addr, detail):
    cs.append((kind, addr, detail))


pcts = [('brk_p', 'brk_t', 'brk'), ('b02_p', 'b02_t', 'b02'), ('b0205_p', 'b0205_t', 'b0205'), ('b05_p', 'b05_t', 'b05'),
        ('sw_p', 'sw_r', 'sw'), ('im_p', 'im_r', 'im'), ('br_p', 'br_r', 'br'), ('fs_p', 'fs_r', 'fs')]
nrow = 0
for tf, rows in data.items():
    n = SH[tf][0]
    for r, name, d in rows:
        nrow += 1
        for p, t, c in pcts:
            exp = 0 if d[c] == 0 else d[t] / d[c]
            if abs(exp - d[p]) > 1e-9:
                bad('pct', f"{n}!{colL[p]}{r}", f'{d[p]} vs {exp}')
            if d[t] > d[c]:
                bad('succ>n', f"{n}!{colL[t]}{r}", f'{d[t]}>{d[c]}')
        if d['brk'] + d['sw'] > d['touch']:
            bad('brk+sw>touch', f"{n}!{r}", f"{d['brk']}+{d['sw']}>{d['touch']}")
        if d['im'] > d['sw'] or d['br'] > d['sw']:
            bad('im|br>sw', f"{n}!{r}", '')
        if d['fs'] > min(d['im'], d['br']):
            bad('fs>min(im,br)', f"{n}!{r}", f"{d['fs']} {d['im']} {d['br']}")
        if d['fs'] < d['im'] + d['br'] - d['sw']:
            bad('fs<im+br-sw', f"{n}!{r}", '')
        if d['b0205'] + d['b05'] != d['brk']:
            bad('brk!=b0205+b05', f"{n}!{r}", f"{d['b0205']}+{d['b05']} vs {d['brk']}")
        if d['brk'] + d['b02'] + d['sw'] != d['touch']:
            bad('touch!=brk+b02+sw', f"{n}!{r}", f"{d['brk']}+{d['b02']}+{d['sw']} vs {d['touch']}")
        if d['brk_t'] != d['b0205_t'] + d['b05_t']:
            bad('brk_t!=b0205_t+b05_t', f"{n}!{r}", '')
    for c in [x for x in COLS if not x.endswith('_p')]:
        s = sum(d[c] for _, _, d in rows)
        if s != tot[tf][c]:
            bad('sum!=ИТОГО', f"{n}!{colL[c]}", f'{s} vs {tot[tf][c]}')
say('rows checked', nrow, 'violations', len(cs), dict(Counter(k for k, _, _ in cs)))
for tf in tot:
    t = tot[tf]
    say('PART', tf, 'touch', t['touch'], 'brk', t['brk'], 'sw', t['sw'], 'touch-brk-sw', t['touch'] - t['brk'] - t['sw'],
        'b02+b0205+b05', t['b02'] + t['b0205'] + t['b05'], 'brk_t', t['brk_t'], 'brk_p', round(t['brk_p'], 4))
    say('  b0205_t+b05_t', t['b0205_t'] + t['b05_t'], 'b02_t+b0205_t+b05_t', t['b02_t'] + t['b0205_t'] + t['b05_t'])
    say('  touch-sw', t['touch'] - t['sw'], 'brk/(touch-sw)', round(t['brk'] / (t['touch'] - t['sw']), 4))
for tf, rows in data.items():
    say('RANGE', tf, 'min touch', min((d['touch'], nm) for _, nm, d in rows), 'max', max((d['touch'], nm) for _, nm, d in rows),
        '0/100% p:', [(nm, p) for _, nm, d in rows for p in ('brk_p', 'b02_p', 'b0205_p', 'b05_p', 'sw_p', 'im_p', 'br_p', 'fs_p') if d[p] in (0, 1)][:15])
    for c in ('b02', 'b0205', 'b05', 'im', 'br', 'fs'):
        sm = [(d[c], nm) for _, nm, d in rows if d[c] < 30]
        say('SMALL n<30', tf, c, len(sm), sm[:6])
S = {tf: [nm for _, nm, _ in rows] for tf, rows in data.items()}
say('assets', {k: len(x) for k, x in S.items()}, 'dups', {k: len(x) - len(set(x)) for k, x in S.items()})
say('1D<=4H', set(S['1D']) <= set(S['4H']), '4H<=1H', set(S['4H']) <= set(S['1H']), '1D<=1H', set(S['1D']) <= set(S['1H']))
say('1H not 4H', sorted(set(S['1H']) - set(S['4H'])))
say('4H not 1D', sorted(set(S['4H']) - set(S['1D'])))
say('1H order', S['1H'][:8], '...', S['1H'][-4:])

ws = v['Все ТФ · по активам']
look = {(tf, nm): d for tf, rows in data.items() for _, nm, d in rows}
allrows = []
k = 0
for r in range(3, ws.max_row + 1):
    vals = [ws.cell(r, c).value for c in range(1, 28)]
    if vals[0] is None:
        if any(x is not None for x in vals):
            say('Все ТФ stray', r, vals)
        continue
    allrows.append((r, vals))
    d = dict(zip(COLS, vals[2:27]))
    key = (vals[1], vals[0])
    if key not in look:
        bad('allTF row not in TF sheet', f'Все ТФ!{r}', str(key))
        continue
    for c in COLS:
        k += 1
        if abs((d[c] or 0) - look[key][c]) > 1e-9:
            bad('allTF!=TF', f'Все ТФ!{r}:{c}', f'{d[c]} vs {look[key][c]}')
rev = set((vals[1], vals[0]) for _, vals in allrows)
say('Все ТФ rows', len(allrows), 'expected', sum(len(x) for x in S.values()), 'cells', k, 'missing', [x for x in look if x not in rev][:5],
    Counter(vals[1] for _, vals in allrows), 'col AA', [(r, vals[26]) for r, vals in allrows if vals[26] is not None][:3])
ws = v['Кратко']
mm = 0
for i, (r, nm, d) in enumerate(data['1H']):
    row = [ws.cell(i + 2, c).value for c in range(1, 5)]
    if row[0] != nm or abs(row[1] - d['brk_p']) > 1e-9 or abs(row[2] - d['sw_p']) > 1e-9 or abs(row[3] - d['fs_p']) > 1e-9:
        mm += 1
say('Кратко rows', ws.max_row - 1, 'mismatch vs 1H', mm)
sv = v['Сводка']
for r in range(1, 30):
    x = [sv.cell(r, c).value for c in range(1, 5)]
    if x[0]:
        say('СВОДКА', r, x)


def wil(k, n, z=1.959964):
    p = k / n
    den = 1 + z * z / n
    c = (p + z * z / (2 * n)) / den
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return max(0, c - h), min(1, c + h)


def nor(k, n, z=1.959964):
    p = k / n
    h = z * math.sqrt(p * (1 - p) / n)
    return max(0, p - h), min(1, p + h)


def cp(k, n, a=0.05):
    return (0 if k == 0 else beta.ppf(a / 2, k, n - k + 1)), (1 if k == n else beta.ppf(1 - a / 2, k + 1, n - k))


def parse(s):
    m = re.match(r'([\d.]+)–([\d.]+) %', s)
    return float(m[1]), float(m[2])


ci = []
sc = Counter()


def chk(addr, n, p, s):
    k = round(p * n)
    lo, hi = parse(s)
    fit = {}
    for nm_, fn in (('wilson', wil), ('normal', nor), ('cp', cp)):
        a, b = fn(k, n)
        fit[nm_] = abs(round(a * 100, 1) - lo) < 0.051 and abs(round(b * 100, 1) - hi) < 0.051
    best = [m for m, ok in fit.items() if ok]
    sc[tuple(best)] += 1
    ci.append((addr, n, p, k, round(abs(k - p * n), 3), s, '/'.join(best) or 'none',
               [round(x * 100, 1) for x in wil(k, n)], [round(x * 100, 1) for x in nor(k, n)], [round(x * 100, 1) for x in cp(k, n)]))


w = v['Пробой по ТФ']
for r in range(3, 10):
    chk(f'Пробой по ТФ!D{r}', w.cell(r, 2).value, w.cell(r, 3).value, w.cell(r, 4).value)
    chk(f'Пробой по ТФ!G{r}', w.cell(r, 5).value, w.cell(r, 6).value, w.cell(r, 7).value)
w = v['Свип по ТФ']
for base in (4, 15):
    for r in range(base, base + 7):
        for nc, pc, ic in ((2, 3, 4), (7, 8, 9), (11, 12, 13), (14, 15, 16)):
            chk(f'Свип по ТФ!{openpyxl.utils.get_column_letter(ic)}{r}', w.cell(r, nc).value, w.cell(r, pc).value, w.cell(r, ic).value)
say('CI method fit', dict(sc), 'cells', len(ci))
json.dump(ci, open(OUT + '-ci.json', 'w', encoding='utf-8'), ensure_ascii=False)
for c in ci:
    if c[6] != 'wilson':
        say('CI not-wilson-only', c)
for base, mode in ((4, '5'), (15, '3')):
    for r in range(base, base + 7):
        B = w.cell(r, 2).value
        say('SHARE', mode, w.cell(r, 1).value, 'B', B, 'F*B', round(w.cell(r, 6).value * B, 1), 'G', w.cell(r, 7).value,
            'J*B', round(w.cell(r, 10).value * B, 1), 'K', w.cell(r, 11).value, 'N', w.cell(r, 14).value)
pb = v['Пробой по ТФ']
for tf in ('1H', '4H', '1D'):
    r = [pb.cell(i, 1).value for i in range(3, 10)].index(tf) + 3
    w5 = [w.cell(i, 1).value for i in range(4, 11)].index(tf) + 4
    w3 = w5 + 11
    t = tot[tf]
    say('RATIO', tf, 'main brk/perTF', round(t['brk'] / pb.cell(r, 2).value, 2), '(b0205+b05)/perTF B', round((t['b0205'] + t['b05']) / pb.cell(r, 2).value, 2),
        'main b05/perTF E', round(t['b05'] / pb.cell(r, 5).value, 2), 'main sw/perTF5', round(t['sw'] / w.cell(w5, 2).value, 2),
        'sw/perTF3', round(t['sw'] / w.cell(w3, 2).value, 2), 'touch/perTF5 sw', round(t['touch'] / w.cell(w5, 2).value, 2))


def der(label, d, addr):
    sw, im, br, fs = d['sw'], d['im'], d['br'], d['fs']
    rs, ri_, rb_, rf = d['sw_r'], d['im_r'], d['br_r'], d['fs_r']
    n0 = sw - im - br + fs
    r0 = rs - ri_ - rb_ + rf
    oi, ri = im - fs, ri_ - rf
    ob, rb = br - fs, rb_ - rf
    rt = lambda a, b: a / b if b else float('nan')
    return dict(label=label, addr=addr, sw=sw, p_all=rt(rs, sw), n_neither=n0, r_neither=r0, p_neither=rt(r0, n0), share_neither=rt(n0, sw),
                n_only_im=oi, p_only_im=rt(ri, oi), n_only_br=ob, p_only_br=rt(rb, ob), n_both=fs, p_both=rt(rf, fs), share_both=rt(fs, sw),
                share_im=rt(im, sw), share_br=rt(br, sw), indep_both=rt(im, sw) * rt(br, sw),
                lift=rt(rt(fs, sw), rt(im, sw) * rt(br, sw)), n_nobr=sw - br, p_nobr=rt(rs - rb_, sw - br), n_noim=sw - im, p_noim=rt(rs - ri_, sw - im),
                p_im=rt(ri_, im), p_br=rt(rb_, br))


rows5 = []
for tf in tot:
    rows5.append(der(tf + ' ИТОГО', tot[tf], f"{SH[tf][0]}!{SH[tf][2]+1}"))
for tf, rows in data.items():
    for r, nm, d in rows:
        rows5.append(der(f'{tf} {nm}', d, f'{SH[tf][0]}!{r}'))
for base, mode in ((4, '5св'), (15, '3св')):
    for r in range(base, base + 7):
        B = w.cell(r, 2).value
        d = dict(sw=B, sw_r=round(w.cell(r, 3).value * B), im=w.cell(r, 7).value, im_r=round(w.cell(r, 8).value * w.cell(r, 7).value),
                 br=w.cell(r, 11).value, br_r=round(w.cell(r, 12).value * w.cell(r, 11).value), fs=w.cell(r, 14).value,
                 fs_r=round(w.cell(r, 15).value * w.cell(r, 14).value))
        rows5.append(der(f'ПоТФ {mode} {w.cell(r,1).value}', d, f'Свип по ТФ!{r}'))
keys = list(rows5[0].keys())
with open(OUT + '-derived.csv', 'w', newline='', encoding='utf-8') as fh:
    wr = csv.writer(fh)
    wr.writerow(keys)
    for x in rows5:
        wr.writerow([round(y, 5) if isinstance(y, float) else y for y in x.values()])
for x in rows5:
    if 'ИТОГО' in x['label'] or x['label'].startswith('ПоТФ'):
        say('DER', {k_: (round(y, 4) if isinstance(y, float) else y) for k_, y in x.items() if k_ != 'addr'})
pn = [(x['p_neither'], x['n_neither'], x['label']) for x in rows5 if x['label'].startswith('1H ') and 'ИТОГО' not in x['label']]
say('1H per-coin p_neither min', min(pn), 'max', max(pn), 'median', round(statistics.median(p for p, _, _ in pn), 4), 'n coins', len(pn),
    'share with p<0.5', sum(1 for p, _, _ in pn if p < 0.5))
say('1H per-coin lift median', round(statistics.median(x['lift'] for x in rows5 if x['label'].startswith('1H ') and 'ИТОГО' not in x['label']), 3))
t = tot['1H']
say('VIDEO 1H', {'781692': t['touch'], '238801': t['brk'], '61%': round(t['brk_p'], 4), '444269': t['sw'], '62.2%': round(t['sw_p'], 4),
                 '125362': t['im'], '84.1%': round(t['im_p'], 4), '128067': t['br'], '89%': round(t['br_p'], 4), '68497': t['fs'], '91.6%': round(t['fs_p'], 4)})
with open(OUT + '-violations.csv', 'w', newline='', encoding='utf-8') as fh:
    wr = csv.writer(fh)
    wr.writerow(['kind', 'addr', 'detail'])
    wr.writerows(cs)
open(OUT + '-audit-log.txt', 'w', encoding='utf-8').write('\n'.join(L))
