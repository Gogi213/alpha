#!/usr/bin/env python3
"""TK-044: вердикт посуточно = целостность (тесты 1–2 + журнал) и gate_ab (В-177/В-179); (в) — числом.
tk044-report.py <run-каталог с w-*.out> <g-days.csv> <полнота.csv> <выход.csv>; сводки — в stdout."""
import csv, glob, re, sys, collections
run, gdays, comp, out = sys.argv[1:5]
pat = re.compile(r'verify: part=(\S+) files=\d+ updates=(\d+) gaps=(\d+) invariants=(\d+) trades=(\d+) out_of_range=\d+ violations=(\d+)')
o = {}
for f in glob.glob(run + '/w-*.out'):
    sym = None
    for line in open(f, encoding='utf-8', errors='replace'):
        m = pat.match(line)
        if m:
            o[m.group(1)] = tuple(int(x) for x in m.groups()[1:])
g = {(r['symbol'], r['part']): r for r in csv.DictReader(open(gdays, encoding='utf-8'))}
rows = list(csv.DictReader(open(comp, encoding='utf-8')))
badm = {(r['sym'], r['month']) for r in rows if r['verify'] == 'fail'}
res = []
for r in rows:
    if r['cat'] != 'pool' or r['verify'] == '': continue
    part = f"{r['sym']}-{r['day']}.binlog"
    s = o.get(part); gd = g.get((r['sym'], part))
    if s[3] == 0 and s[0] <= 1:
        res.append(dict(sym=r['sym'], day=r['day'], month=r['month'], in57='0', old=r['verify'], trades=0, violations=0, ppm=0,
            gaps=s[1], invariants=s[2], verdict='пусто', reason='в файле ≤1 обновления и 0 сделок — проверять нечего'))
        continue
    if s is None or gd is None:
        res.append(dict(sym=r['sym'], day=r['day'], month=r['month'], old=r['verify'], verdict='нет окон', reason='нет строки окон/сводки'))
        continue
    upd, gaps, inv, tr, vi = s
    integ = gaps == 0 and inv == 0
    t3fail = tr > 0 and vi * 1000 >= tr
    old = r['verify']
    journal = old == 'fail' and integ and not t3fail
    why = []
    if not integ: why.append('тесты 1–2 (gaps/инварианты)')
    if journal: why.append('журнал потерь записи')
    if gd['gate_ab'] == 'fail': why.append('гейт ' + ''.join(c for c in gd['signs'] if c in 'ab'))
    why_b = [w_ for w_ in why if not w_.startswith('гейт')] + (['гейт b'] if 'b' in gd['signs'] else [])
    res.append(dict(sym=r['sym'], day=r['day'], month=r['month'], in57='1' if (r['sym'], r['month']) in badm else '0',
        old=old, trades=tr, violations=vi, ppm=(vi * 1000000 // tr if tr else 0), t3_over_0p1pct='1' if t3fail else '0',
        gaps=gaps, invariants=inv, gate_ab=gd['gate_ab'], gate_all=gd['gate'], signs=gd['signs'], first_window=gd['first_window'],
        vw=gd['vw'], verdict='не пускаем' if why_b else 'пускаем', reason='; '.join(why_b),
        verdict_ab='не пускаем' if why else 'пускаем', reason_ab='; '.join(why)))
cols = ['sym','day','month','in57','old','trades','violations','ppm','t3_over_0p1pct','gaps','invariants','gate_ab','gate_all','signs','first_window','vw','verdict','reason','verdict_ab','reason_ab']
with open(out, 'w', newline='', encoding='utf-8') as fh:
    w = csv.DictWriter(fh, cols); w.writeheader(); w.writerows(res)
def tab(title, rs):
    print('##', title)
    by = collections.defaultdict(collections.Counter)
    for x in rs:
        c = by[x['month']]; c['n'] += 1; c['old_ok'] += x['old'] == 'ok'; c['old_fail'] += x['old'] == 'fail'
        c['allow'] += x['verdict'] == 'пускаем'; c['block'] += x['verdict'] == 'не пускаем'; c['none'] += x['verdict'] in ('нет окон', 'пусто')
        c['t3only_now_allowed'] += x['old'] == 'fail' and x['verdict'] == 'пускаем'
        c['oldok_now_blocked'] += x['old'] == 'ok' and x['verdict'] == 'не пускаем'
        c['gate_ab_fail'] += x.get('gate_ab') == 'fail'; c['gate_ab_on_oldok'] += x.get('gate_ab') == 'fail' and x['old'] == 'ok'
    print('month,days,old_ok,old_fail,allow,block,no_windows,old_fail_now_allowed,old_ok_now_blocked,gate_ab_fail,gate_ab_fail_on_old_ok')
    t = collections.Counter()
    for m in sorted(by):
        c = by[m]; t.update(c)
        print(m, c['n'], c['old_ok'], c['old_fail'], c['allow'], c['block'], c['none'], c['t3only_now_allowed'], c['oldok_now_blocked'], c['gate_ab_fail'], c['gate_ab_on_oldok'], sep=',')
    print('ИТОГО', t['n'], t['old_ok'], t['old_fail'], t['allow'], t['block'], t['none'], t['t3only_now_allowed'], t['oldok_now_blocked'], t['gate_ab_fail'], t['gate_ab_on_oldok'], sep=',')
def tab_b(title, rs):
    print('##', title)
    by = collections.defaultdict(collections.Counter)
    for x in rs:
        if x['verdict'] in ('нет окон', 'пусто'): continue
        c = by[x['month']]; c['n'] += 1; c['allow_ab'] += x['verdict_ab'] == 'пускаем'; c['allow_b'] += x['verdict'] == 'пускаем'
        c['block_b'] += x['verdict'] == 'не пускаем'
        c['oldfail_allowed_b'] += x['old'] == 'fail' and x['verdict'] == 'пускаем'; c['oldok_blocked_b'] += x['old'] == 'ok' and x['verdict'] == 'не пускаем'
    print('month,days,allow_a+b,allow_b_only,block_b_only,old_fail_now_allowed_b,old_ok_now_blocked_b'); t = collections.Counter()
    for m in sorted(by):
        c = by[m]; t.update(c); print(m, c['n'], c['allow_ab'], c['allow_b'], c['block_b'], c['oldfail_allowed_b'], c['oldok_blocked_b'], sep=',')
    print('ИТОГО', t['n'], t['allow_ab'], t['allow_b'], t['block_b'], t['oldfail_allowed_b'], t['oldok_blocked_b'], sep=',')
tab_b('ВЕРДИКТ (В-181) = только (б), (а) числом; рядом вариант (а)+(б) — все сутки пула', res)
tab_b('ВЕРДИКТ (В-181) = только (б) — монето-месяцы с битыми сутками', [x for x in res if x.get('in57') == '1'])
tab('все сутки пула', res)
tab('только монето-месяцы с битыми сутками (in57)', [x for x in res if x.get('in57') == '1'])
rc = collections.Counter(x['reason'] for x in res if x['verdict'] == 'не пускаем')
print('## причины блокировки'); [print(k, v) for k, v in rc.most_common()]

print('## пустые сутки (updates<=1, trades=0): ', sum(1 for x in res if x['verdict'] == 'пусто'),
      '; нет окон/сводки:', sum(1 for x in res if x['verdict'] == 'нет окон'))
print('## сверка со списком полноты: pool verify=fail', sum(1 for r in rows if r['cat'] == 'pool' and r['verify'] == 'fail'),
      '| в вердикте old=fail', sum(1 for x in res if x['old'] == 'fail'),
      '| pool без verify (не берём)', sum(1 for r in rows if r['cat'] == 'pool' and r['verify'] == ''),
      '| carry fail (вне пула, в 1147)', sum(1 for r in rows if r['cat'] == 'carry' and r['verify'] == 'fail'))
# концентрация: монеты, у которых блок > 50 % суток
bc = collections.defaultdict(lambda: [0, 0, collections.Counter()])
for x in res:
    if x['verdict'] in ('нет окон', 'пусто'): continue
    c = bc[x['sym']]; c[0] += 1
    if x['verdict'] == 'не пускаем': c[1] += 1; c[2][x['month']] += 1
blk = sum(c[1] for c in bc.values())
hot = sorted(((s_, c) for s_, c in bc.items() if c[1] * 2 > c[0]), key=lambda t: -t[1][1])
print('## концентрация: монет с блоком >50 %% суток: %d, блок-суток у них %d из %d (%.0f %%)' % (len(hot), sum(c[1] for _, c in hot), blk, 100 * sum(c[1] for _, c in hot) / max(1, blk)))
for s_, c in hot[:40]: print('  ', s_, f'{c[1]}/{c[0]}', ' '.join(f'{m[5:]}:{n}' for m, n in sorted(c[2].items())))
rest = [(s_, c) for s_, c in bc.items() if c[1] * 2 <= c[0]]
print('## остальные монеты: блок-суток', sum(c[1] for _, c in rest), 'из', sum(c[0] for _, c in rest), '; монет с блоком >0:', sum(1 for _, c in rest if c[1]))
bm = collections.Counter(); 
for x in res:
    if x['verdict'] == 'не пускаем' and bc[x['sym']][1] * 2 <= bc[x['sym']][0]: bm[x['month']] += 1
print('## блок вне «горячих» монет по месяцам:', dict(sorted(bm.items())))
