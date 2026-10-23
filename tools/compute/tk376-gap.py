#!/usr/bin/env python3
"""TK-376 (G-016): эпизоды расхождения цены пула DEX и Bybit. Запуск на calc: tk376-gap.py OUT_DIR [P]
Цена пула — последний своп (price_usd, ffill на 1 с; резервов/sqrtPrice в данных нет). CEX — последняя сделка Bybit за секунду
(перп, иначе спот; множитель 1000X учтён), ffill; кэш секундных рядов /data/tk0364/pt/by (как в tk364-pseudotest.py).
gap = (пул-CEX)/CEX в бп. Эпизод: |gap| > 13 бп (TH) — до |gap| <= 13; t30/t5 — время до |gap| <= 30 / <= 5 бп. Комиссия пула — параметр summary (fee пулов этих монет не получен).
Пул-сутки идут в счёт при >= MINSW свопах в сутках (тонкие пулы — артефакт «последнего свопа»). Выход: episodes.csv, DONE.json."""
import sys, os, csv, json, array, collections, multiprocessing as mp, statistics
OUT = sys.argv[1]; P = int(sys.argv[2]) if len(sys.argv) > 2 else 15; OUT0, P0 = OUT, P
_pt = open('/data/tk0364/tk364-pseudotest.py').read().split('def work(job):')[0]
_argv = sys.argv; sys.argv = ['x', '/tmp/tk376-pt-dummy']; exec(_pt); sys.argv = _argv; OUT, P = OUT0, P0  # load_symbols, bybit_sym, day_series, by_series, ROOT, DAYS, D0, MINSW
TH = 13.0   # порог эпизода, бп (fee пулов этих 477 монет не получен: комиссия — в summary как параметр 5/30/100)
META = json.load(open('/data/tk0364/usd/pool-meta.json'))
TAKER = {'perp': 5.5, 'spot': 10.0}   # Bybit VIP0, бп
BLK = {'ethereum': 12, 'base': 2, 'bsc': 3}   # секунд на блок
DELAYS = (1, 5, 'blk')
os.makedirs(OUT, exist_ok=True)
N = 7 * 86400

def work(job):
    dirname, net, bs = job
    sym, kind, mult = bs
    try:
        by = by_series(sym, kind)
        if by is None or len(by) != N: return []
        c = [v / mult for v in by]
        pools = collections.defaultdict(list)
        for d in DAYS:
            p = f'{ROOT}/{net}/{dirname}/2026-10-{d:02d}.csv'
            if not os.path.exists(p): continue
            for r in csv.DictReader(open(p)):
                if r['price_usd']: pools[r['pool']].append((int(r['ts']), int(r['logidx']), float(r['price_usd']), r['dex'], float(r['quote_usd'] or 0), r['tx'], int(r['block'])))
        cm = statistics.median(v for v in c[::600] if v == v) if any(v == v for v in c[::600]) else 0
        out = []
        for pool, rows in pools.items():
            rows.sort()
            ratio = statistics.median(r[2] for r in rows) / cm if cm else 0
            if not (0.8 < ratio < 1.25): continue
            fee = TH
            perday = collections.Counter((r[0] - D0) // 86400 for r in rows)
            okdays = {d for d, k in perday.items() if k >= MINSW}
            if not okdays: continue
            pa = [float('nan')] * N
            sw = array.array('b', [0]) * N      # своп в секунду i
            first = last = None
            for k, r in enumerate(rows):
                i = r[0] - D0
                if not (0 <= i < N): continue
                j = rows[k + 1][0] - D0 if k + 1 < len(rows) else N
                j = min(max(j, i + 1), N)
                pa[i:j] = [r[2]] * (j - i)
                sw[i] = 1
            # сутки без достаточных свопов — вне счёта
            g = [(a - b) / b * 1e4 if (a == a and b == b and b > 0) else float('nan') for a, b in zip(pa, c)]
            ok = [False] * N
            for d in okdays:
                lo = d * 86400
                ok[lo:lo + 86400] = [True] * 86400
            swrows = {}
            for r in rows:
                swrows.setdefault(r[0] - D0, r)
            nsw_day = {d: perday[d] for d in okdays}
            i = 1
            hot = [k for k in range(1, N) if g[k] == g[k] and abs(g[k]) > fee and ok[k]]
            hs = set()
            pos = 0
            while pos < len(hot):
                i = hot[pos]
                # начало эпизода: предыдущая секунда не в эпизоде того же знака
                s = 1 if g[i] > 0 else -1
                if g[i - 1] == g[i - 1] and abs(g[i - 1]) > fee and (g[i - 1] > 0) == (s > 0) and ok[i - 1]:
                    pos += 1; continue
                j = i
                while j < N and g[j] == g[j] and ok[j] and abs(g[j]) > fee and (g[j] > 0) == (s > 0): j += 1
                t30 = next((k - i for k in range(i, j) if abs(g[k]) <= 30), j - i)   # до |gap|<=30 (внутри эпизода 13 бп), иначе = dur
                t5 = ''
                for k in range(j, min(N, j + 3600)):
                    if not ok[k] or g[k] != g[k]: break
                    if abs(g[k]) <= 5: t5 = k - i; break
                cens = int(j >= N or not ok[j] or g[j] != g[j])
                dur = j - i
                seg = g[i:j]
                mx = max(abs(v) for v in seg)
                pc_open = (pa[i] != pa[i - 1]); cc_open = (c[i] != c[i - 1])
                open_by = 'both' if pc_open and cc_open else 'pool' if pc_open else 'cex' if cc_open else 'none'
                if cens: close_by = 'cens'; one = 0; share = ''
                else:
                    pc = pa[j] != pa[j - 1]; cc = c[j] != c[j - 1]
                    close_by = 'both' if pc and cc else 'pool' if pc else 'cex' if cc else 'none'
                    gj = (pa[j - 1] - c[j]) / c[j] * 1e4   # старая цена пула, новая CEX
                    one = int(pc and abs(gj) > fee and (gj > 0) == (s > 0))
                    tp = max(-s * (pa[j] - pa[i]) / c[i] * 1e4, 0.0); tc = max(s * (c[j] - c[i]) / c[i] * 1e4, 0.0)
                    share = round(tp / (tp + tc), 3) if tp + tc > 0 else ''
                nsw = sum(sw[i:j])
                r0 = swrows.get(i) or swrows.get(max((t for t in swrows if t <= i), default=-1)) or rows[0]
                d0 = (i // 86400)
                row = dict(net=net, coin=dirname, pool=pool, dex=r0[3], day=15 + d0, ts=D0 + i,
                           side='pool_above' if s > 0 else 'pool_below', open_gap=round(g[i], 2), max_gap=round(mx, 2), dur_s=dur, cens=cens,
                           open_by=open_by, close_by=close_by, one_swap_close=one, pool_share=share, t30=t30, t5=t5, nsw=nsw, sw_day=nsw_day.get(d0, 0),
                           swap_usd=round(r0[4], 1), tx=r0[5], block=r0[6], bkind=kind)
                tk = TAKER[kind]
                for dl in DELAYS:
                    dd = BLK[net] if dl == 'blk' else dl
                    rem = s * g[i + dd] if i + dd < N and g[i + dd] == g[i + dd] else float('nan')
                    row[f'rem_{dl}'] = round(rem, 2) if rem == rem else ''
                    row[f'net_{dl}'] = round(rem - tk, 2) if rem == rem else ''  # без комиссии пула (вычитается в summary)
                out.append(row)
                pos += 1
                # пропустить остаток эпизода
                while pos < len(hot) and hot[pos] < j: pos += 1
        return out
    except Exception as e:
        return [dict(net=net, coin=dirname, pool='ERR', dex=repr(e)[:100])]

def main():
    perp, spot = load_symbols()
    jobs = []
    for net in ('ethereum', 'base', 'bsc'):
        for dn in sorted(os.listdir(f'{ROOT}/{net}')):
            bs = bybit_sym(dn.split('-')[0], perp, spot)
            if bs: jobs.append((dn, net, bs))
    print('jobs', len(jobs), flush=True)
    rows = []
    with mp.Pool(P) as pool:
        for k, r in enumerate(pool.imap_unordered(work, jobs), 1):
            rows += r
            if k % 20 == 0: print(k, len(jobs), len(rows), flush=True)
    cols = ['net', 'coin', 'pool', 'dex', 'day', 'ts', 'side', 'open_gap', 'max_gap', 'dur_s', 'cens', 'open_by', 'close_by',
            'one_swap_close', 'pool_share', 't30', 't5', 'nsw', 'sw_day', 'swap_usd', 'tx', 'block', 'bkind'] + [f'{a}_{d}' for d in DELAYS for a in ('rem', 'net')]
    with open(f'{OUT}/episodes.csv', 'w', newline='') as f:
        w = csv.DictWriter(f, cols, extrasaction='ignore'); w.writeheader(); w.writerows([r for r in rows if r.get('pool') != 'ERR'])
    errs = [r for r in rows if r.get('pool') == 'ERR']
    open(f'{OUT}/DONE.json', 'w').write(json.dumps({'jobs': len(jobs), 'episodes': len(rows) - len(errs), 'errors': len(errs), 'err_sample': errs[:3]}))

if __name__ == '__main__': main()
