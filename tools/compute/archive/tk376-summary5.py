#!/usr/bin/env python3
"""TK-376 проход 4 (= summary3 + страты по open_gap вместо max_gap + сводка контроля свопов ctrl_sw.csv, argv[5]). Проход 3: сводка эпизодов (mid Bybit). usage: tk376-summary3.py episodes.csv ctrl.csv OUT_PREFIX [gas.json] -> OUT_PREFIX-strata.csv, OUT_PREFIX-ctrl.csv, текст в stdout
Добавлено к tk376-summary.py: страты по «капу» (оборот Bybit 24 ч, tk364 bybit-ticks — прокси, рыночной капитализации в данных нет), по монетам, доля закрытия в том же блоке,
интервал среднего net_1 по монето-суткам (bootstrap 400, кластер = монета-сутки), net с газом (gas.json: {net: usd на своп}, размер входа 1000 USD), контроль ctrl.csv."""
import sys, csv, collections, statistics, random, json
CTRL = sys.argv[2]; PFX = sys.argv[3]; GAS = json.load(open(sys.argv[4])) if len(sys.argv) > 4 and sys.argv[4] != '-' else {}; CSW = sys.argv[5] if len(sys.argv) > 5 else None
SIZE = 1000.0
TURN = {}
for _r in csv.reader(open('/data/tk0364/bybit-ticks.csv', encoding='utf-8-sig'), delimiter=';'):
    if _r[1] != 'symbol' and _r[1].endswith('USDT'): TURN[_r[1][:-4]] = max(TURN.get(_r[1][:-4], 0), float(_r[6] or 0))
rows = list(csv.DictReader(open(sys.argv[1])))
for r in rows:
    b = r['coin'].split('-')[0]; t = TURN.get(b) or TURN.get('1000' + b) or TURN.get('10000' + b) or TURN.get('1000000' + b) or 0
    r['cap'] = 'turn<5M' if t < 5e6 else 'turn<50M' if t < 5e7 else 'turn>=50M'
    r['same_blk'] = 1 if (r['close_block'] != '' and r['close_block'] == r['block']) else 0
for r in rows:
    for k in ('open_gap', 'max_gap', 'dur_s', 'cens', 'one_swap_close', 'nsw', 'sw_day', 'swap_usd', 'day', 'fee_bp'):
        r[k] = float(r[k])
    for k in ('rem_1', 'rem_5', 'rem_blk', 'net_1', 'net_5', 'net_blk', 'pool_share', 't30', 't5'):
        r[k] = float(r[k]) if r[k] != '' else None
def q(xs, ps=(0.1, 0.25, 0.5, 0.75, 0.9, 0.95, 0.99)):
    xs = sorted(xs)
    return [xs[min(len(xs) - 1, int(p * len(xs)))] for p in ps] if xs else [None] * len(ps)
def f(x): return '' if x is None else (f'{x:.1f}' if abs(x) < 1000 else f'{x:.0f}')
def stat(rs, name, out):
    if not rs: return
    done = [r for r in rs if not r['cens']]
    ds = q([r['dur_s'] for r in done]); gm = q([r['max_gap'] for r in rs], (0.5, 0.9, 0.99))
    cd = len({(r['coin'], r['net'], r['day']) for r in rs})
    t30 = q([r['t30'] for r in done if r['t30'] is not None], (0.5, 0.9)); t5 = [r['t5'] for r in done if r['t5'] is not None]
    pc = [r['pool_share'] for r in done if r['pool_share'] is not None]
    o = dict(strat=name, n=len(rs), coin_days=cd, cens=round(sum(r['cens'] for r in rs) / len(rs), 3),
             gap_p50=gm[0], gap_p90=gm[1], gap_p99=gm[2], n30=sum(1 for r in rs if r['max_gap'] > 30), n50=sum(1 for r in rs if r['max_gap'] > 50), n100=sum(1 for r in rs if r['max_gap'] > 100),
             t30_p50=t30[0], t30_p90=t30[1], t5_frac=round(len(t5) / max(len(done), 1), 3), t5_p50=q(t5, (0.5,))[0] if t5 else '',
             dur_p10=ds[0], dur_p50=ds[2], dur_p90=ds[4], dur_p99=ds[6],
             close_pool=round(sum(1 for r in done if r['close_by'] in ('pool', 'both')) / max(len(done), 1), 3),
             close_cex_only=round(sum(1 for r in done if r['close_by'] == 'cex') / max(len(done), 1), 3),
             open_pool=round(sum(1 for r in rs if r['open_by'] in ('pool', 'both')) / len(rs), 3),
             one_swap=round(sum(r['one_swap_close'] for r in done) / max(len(done), 1), 3),
             pool_share_mean=round(statistics.mean(pc), 3) if pc else '')
    # same_blk_close убран (проход 5): ts свопа = ts блока, закрытие — в более позднюю секунду => 0 по построению
    for k in ('1', '5', 'blk'):
        v = [r[f'net_{k}'] for r in rs if r[f'net_{k}'] is not None]   # уже за вычетом taker CEX и fee пула
        if GAS:
            gv = [r[f'net_{k}'] - GAS.get(r['net'], 0) / SIZE * 1e4 for r in rs if r[f'net_{k}'] is not None]
            o[f'netgas{k}_p50'] = statistics.median(gv) if gv else ''
            o[f'netgas{k}_pos'] = round(sum(1 for x in gv if x > 0) / len(gv), 3) if gv else ''
        o[f'net{k}_p50'] = statistics.median(v) if v else ''
        o[f'net{k}_pos'] = round(sum(1 for x in v if x > 0) / len(v), 3) if v else ''
        o[f'net{k}_mean'] = round(statistics.mean(v), 2) if v else ''
        if k == '1' and len(rs) >= 30:   # интервал медианы net_1 по монето-суткам (bootstrap)
            cl = collections.defaultdict(lambda: [0.0, 0])
            for r in rs:
                if r['net_1'] is not None: c0 = cl[(r['coin'], r['net'], r['day'])]; c0[0] += r['net_1']; c0[1] += 1
            cv = list(cl.values()); rnd = random.Random(7); ms = []
            for _ in range(400):
                smp = [cv[rnd.randrange(len(cv))] for _ in cv]
                ms.append(sum(x[0] for x in smp) / max(sum(x[1] for x in smp), 1))
            ms.sort(); o['net1_mean_lo'] = round(ms[10], 2); o['net1_mean_hi'] = round(ms[389], 2); o['net1_clusters'] = len(cv)
    out.append(o)
out = []
stat(rows, 'ALL', out)
def ftier(r): return 'fee<=5' if r['fee_bp'] <= 5 else 'fee<=25' if r['fee_bp'] <= 25 else 'fee<=30' if r['fee_bp'] <= 30 else 'fee>30'
for v in ('fee<=5', 'fee<=25', 'fee<=30', 'fee>30'): stat([r for r in rows if ftier(r) == v], v, out)
for key in ('net', 'dex', 'bkind', 'day', 'open_by', 'close_by', 'side', 'cap'):
    for v in sorted({r[key] for r in rows}, key=str): stat([r for r in rows if r[key] == v], f'{key}={v}', out)
def tier(r): return 'sw_day<200' if r['sw_day'] < 200 else 'sw_day<1000' if r['sw_day'] < 1000 else 'sw_day>=1000'
for v in ('sw_day<200', 'sw_day<1000', 'sw_day>=1000'): stat([r for r in rows if tier(r) == v], v, out)
for lo, hi in ((0, 20), (20, 30), (30, 50), (50, 100), (100, 1e9)): stat([r for r in rows if lo <= abs(r['open_gap']) < hi], f'open_gap[{lo},{hi})', out)
for lo, hi in ((0, 5), (5, 30), (30, 300), (300, 1e9)): stat([r for r in rows if lo <= r['dur_s'] < hi], f'dur[{lo},{hi})', out)
bycoin = collections.defaultdict(list)
for r in rows: bycoin[(r['coin'], r['net'])].append(r)
for (c, n), rs in sorted(bycoin.items(), key=lambda kv: -len(kv[1]))[:15]: stat(rs, f'coin={c}@{n}', out)
# по монетам: самые длинные и самые широкие (медиана длительности при n >= 30)
cl = [(statistics.median(r['dur_s'] for r in rs if not r['cens']) if any(not r['cens'] for r in rs) else 0, k, len(rs)) for k, rs in bycoin.items() if len(rs) >= 30]
cl.sort(reverse=True)
with open(f'{PFX}-strata.csv', 'w', newline='') as fh:
    cols = list(out[0].keys())
    w = csv.DictWriter(fh, cols); w.writeheader(); w.writerows(out)
for o in out:
    print(' | '.join(f'{k}={f(v) if isinstance(v, (int, float)) else v}' for k, v in o.items()))
print('LONGEST median dur coins (n>=30):', [(f(a), k[0] + '@' + k[1], n) for a, k, n in cl[:8]])

# контроль: |gap| во все секунды пул-суток против эпизодов
cr = list(csv.DictReader(open(CTRL)))
def wq2(sel, key, p=0.5):
    xs = sorted((float(r[key]), float(r['n'])) for r in sel); tot = sum(w for _, w in xs); acc = 0
    for v, w in xs:
        acc += w
        if acc >= p * tot: return v
def wq(key, ws, p=0.5):
    xs = sorted((float(r[key]), float(r['n'])) for r in cr if ws(r)); tot = sum(w for _, w in xs); acc = 0
    for v, w in xs:
        acc += w
        if acc >= p * tot: return v
    return None
cout = []
for name, ws in (('ALL', lambda r: True), ('sw_day<200', lambda r: float(r['sw_day']) < 200), ('sw_day>=1000', lambda r: float(r['sw_day']) >= 1000), ('ethereum', lambda r: r['net'] == 'ethereum'), ('base', lambda r: r['net'] == 'base'), ('bsc', lambda r: r['net'] == 'bsc')):
    sel = [r for r in cr if ws(r)]
    if not sel: continue
    tw = sum(float(r['n']) for r in sel)
    cout.append(dict(strat=name, pool_days=len(sel), seconds=int(tw), ctrl_q50_med=wq('q50', ws), ctrl_q90_med=wq('q90', ws), ctrl_q99_med=wq('q99', ws),
                     share_gt_fee=round(sum(float(r['sh_fee']) * float(r['n']) for r in sel) / tw, 4), share_gt50=round(sum(float(r['sh50']) * float(r['n']) for r in sel) / tw, 4), share_gt100=round(sum(float(r['sh100']) * float(r['n']) for r in sel) / tw, 4)))
with open(f'{PFX}-ctrl.csv', 'w', newline='') as fh:
    w = csv.DictWriter(fh, list(cout[0].keys())); w.writeheader(); w.writerows(cout)
for o in cout: print('CTRL', ' | '.join(f'{k}={v}' for k, v in o.items()))

# контроль свопов (проход 4): те же секунды со свопом, та же мера
if CSW:
    cs = list(csv.DictReader(open(CSW))); cso = []
    for name, ws in (('ALL', lambda r: True), ('sw_day<200', lambda r: float(r['sw_day']) < 200), ('sw_day>=1000', lambda r: float(r['sw_day']) >= 1000), ('ethereum', lambda r: r['net'] == 'ethereum'), ('base', lambda r: r['net'] == 'base'), ('bsc', lambda r: r['net'] == 'bsc')):
        sel = [r for r in cs if ws(r)]
        if not sel: continue
        tw = sum(float(r['n']) for r in sel); nn = sum(int(r['nn']) for r in sel); nc = sum(int(r['nc']) for r in sel)
        # интервал среднего net по пул-суткам (bootstrap 400)
        cl = [(float(r['nsum']), int(r['nn'])) for r in sel]; rnd = random.Random(7); ms = []
        for _ in range(400):
            smp = [cl[rnd.randrange(len(cl))] for _ in cl]; ms.append(sum(a for a, _ in smp) / max(sum(b for _, b in smp), 1))
        ms.sort()
        cso.append(dict(strat=name, pool_days=len(sel), swaps=int(tw), sh_swaps_gt_fee=round(sum(float(r['sh_fee']) * float(r['n']) for r in sel) / tw, 4),
                        q50_med=wq2(sel, 'q50'), q90_med=wq2(sel, 'q90'), n_hot=nn, net1_mean_hot=round(sum(float(r['nsum']) for r in sel) / max(nn, 1), 2), net1_lo=round(ms[10], 2), net1_hi=round(ms[389], 2),
                        n_hot_cont=nc, net1_mean_hot_cont=round(sum(float(r['ncsum']) for r in sel) / max(nc, 1), 2)))
    with open(f'{PFX}-ctrlsw.csv', 'w', newline='') as fh:
        w = csv.DictWriter(fh, list(cso[0].keys())); w.writeheader(); w.writerows(cso)
    for o in cso: print('CTRLSW', ' | '.join(f'{k}={v}' for k, v in o.items()))
