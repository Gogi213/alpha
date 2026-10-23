#!/usr/bin/env python3
"""TK-376: сводка эпизодов. usage: tk376-summary.py episodes.csv OUT_PREFIX  -> OUT_PREFIX-strata.csv и текст в stdout"""
import sys, csv, collections, statistics
rows = list(csv.DictReader(open(sys.argv[1])))
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
    for k in ('1', '5', 'blk'):
        v = [r[f'net_{k}'] for r in rs if r[f'net_{k}'] is not None]   # уже за вычетом taker CEX и fee пула
        o[f'net{k}_p50'] = statistics.median(v) if v else ''
        o[f'net{k}_pos'] = round(sum(1 for x in v if x > 0) / len(v), 3) if v else ''
        o[f'net{k}_mean'] = round(statistics.mean(v), 2) if v else ''
    out.append(o)
out = []
stat(rows, 'ALL', out)
def ftier(r): return 'fee<=5' if r['fee_bp'] <= 5 else 'fee<=25' if r['fee_bp'] <= 25 else 'fee<=30' if r['fee_bp'] <= 30 else 'fee>30'
for v in ('fee<=5', 'fee<=25', 'fee<=30', 'fee>30'): stat([r for r in rows if ftier(r) == v], v, out)
for key in ('net', 'dex', 'bkind', 'day', 'open_by', 'close_by', 'side'):
    for v in sorted({r[key] for r in rows}, key=str): stat([r for r in rows if r[key] == v], f'{key}={v}', out)
def tier(r): return 'sw_day<200' if r['sw_day'] < 200 else 'sw_day<1000' if r['sw_day'] < 1000 else 'sw_day>=1000'
for v in ('sw_day<200', 'sw_day<1000', 'sw_day>=1000'): stat([r for r in rows if tier(r) == v], v, out)
for lo, hi in ((0, 50), (50, 100), (100, 1e9)): stat([r for r in rows if lo <= r['max_gap'] < hi], f'max_gap[{lo},{hi})', out)
for lo, hi in ((0, 5), (5, 30), (30, 300), (300, 1e9)): stat([r for r in rows if lo <= r['dur_s'] < hi], f'dur[{lo},{hi})', out)
bycoin = collections.defaultdict(list)
for r in rows: bycoin[(r['coin'], r['net'])].append(r)
for (c, n), rs in sorted(bycoin.items(), key=lambda kv: -len(kv[1]))[:15]: stat(rs, f'coin={c}@{n}', out)
# по монетам: самые длинные и самые широкие (медиана длительности при n >= 30)
cl = [(statistics.median(r['dur_s'] for r in rs if not r['cens']) if any(not r['cens'] for r in rs) else 0, k, len(rs)) for k, rs in bycoin.items() if len(rs) >= 30]
cl.sort(reverse=True)
with open(f'{sys.argv[2]}-strata.csv', 'w', newline='') as fh:
    cols = list(out[0].keys())
    w = csv.DictWriter(fh, cols); w.writeheader(); w.writerows(out)
for o in out:
    print(' | '.join(f'{k}={f(v) if isinstance(v, (int, float)) else v}' for k, v in o.items()))
print('LONGEST median dur coins (n>=30):', [(f(a), k[0] + '@' + k[1], n) for a, k, n in cl[:8]])
