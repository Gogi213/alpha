#!/usr/bin/env python3
"""TK-416 п.4б, разбор «цена сделки вне всей глубины ob200»: сырые примеры по одному файлу.

  file <zip> <сделки-корень> <out.tsv>  — итог файла в stdout, до 12 примеров (равномерно по суткам) в out.tsv

Те же правила, что в tk416-ob-trades.py (книга «до сделки» = события с cts < t; окно 2 с назад/вперёд; «вне глубины» =
p < min bid или p > max ask книги в момент сделки). Для каждой такой сделки: сторона тейкера, p против края (тики, бп),
число уровней стороны (200 или меньше), размер сделки против объёма стороны, видели ли цену в книге раньше в файле."""
import sys, json, zipfile, importlib.util, os, statistics

here = os.path.dirname(os.path.abspath(__file__))
sp = importlib.util.spec_from_file_location('trd', os.path.join(here, 'tk416-ob-trades.py'))
trd = importlib.util.module_from_spec(sp)
sp.loader.exec_module(trd)
W = trd.W


def main(path, troot, out):
    mkt = os.path.basename(os.path.dirname(os.path.dirname(path)))
    day, sym = trd.key_of(path)
    tt, tp, tb = trd.load_trades(troot, mkt, sym, day)
    sizes = {}
    # размеры сделок (4-й столбец linear-файла)
    import gzip
    with gzip.open(os.path.join(troot, mkt, sym, '%s%s.csv.gz' % (sym, day)), 'rt') as f:
        next(f)
        sz = [float(ln.split(',', 5)[3]) for ln in f]
    if len(sz) != len(tt):
        sys.exit('размеры не по числу сделок')
    n = len(tt)
    fh = zipfile.ZipFile(path).open(zipfile.ZipFile(path).namelist()[0])
    bids, asks = {}, {}
    gone_b, gone_a = {}, {}
    seen = set()
    ti = 0
    pend = []
    cand = []  # finished: dict
    watch = []  # кандидаты, ждущие, когда цена попадёт в окно книги (до 60 с)
    CW = 60000.0
    prev_te = 0.0
    nl_b, nl_a = [], []
    cnt = dict(buy_above=0, sell_below=0, other=0, all=0)

    def book_state():
        return (min(bids), max(bids), min(asks), max(asks), len(bids), len(asks), sum(bids.values()), sum(asks.values()))

    for raw in trd.lines_of(fh):
        if not raw.strip():
            continue
        d = json.loads(raw)
        te = float(d.get('cts') or d['ts'])
        if te < prev_te:
            te = prev_te
        prev_te = te
        if pend:
            keep = []
            for pd in pend:
                if pd['t'] + W < te:
                    cand.append(pd)
                    watch.append(pd)
                else:
                    keep.append(pd)
            pend = keep
        while ti < n and tt[ti] <= te:
            t, p, buy, s = tt[ti], tp[ti], tb[ti], sz[ti]
            ti += 1
            if not bids and not asks:
                continue
            side = asks if buy else bids
            if p in side:
                continue
            g = (gone_a if buy else gone_b).get(p)
            if g is not None and t - g <= W:
                continue
            if bids and asks:
                lo_b, hi_b, lo_a, hi_a, nb, na, vb, va = book_state()
                if p < lo_b or p > hi_a:
                    pend.append(dict(t=t, p=p, buy=buy, size=s, lo_b=lo_b, hi_b=hi_b, lo_a=lo_a, hi_a=hi_a, nb=nb, na=na, vb=vb,
                                     va=va, seen=(p in seen), gone=(gone_a if buy else gone_b).get(p)))
        typ = d['type']
        dd = d['data']
        if typ == 'snapshot':
            bids = {float(p): float(s) for p, s in dd.get('b', [])}
            asks = {float(p): float(s) for p, s in dd.get('a', [])}
            seen.update(bids)
            seen.update(asks)
        else:
            for p, s in dd.get('b', ()):
                pf = float(p)
                if float(s) == 0.0:
                    if bids.pop(pf, None) is not None:
                        gone_b[pf] = te
                else:
                    bids[pf] = float(s)
                    seen.add(pf)
            for p, s in dd.get('a', ()):
                pf = float(p)
                if float(s) == 0.0:
                    if asks.pop(pf, None) is not None:
                        gone_a[pf] = te
                else:
                    asks[pf] = float(s)
                    seen.add(pf)
        if pend:
            keep = []
            for pd in pend:
                if pd['p'] in (asks if pd['buy'] else bids):
                    continue
                keep.append(pd)
            pend = keep
        if watch and bids and asks:
            lo, hi = min(bids), max(asks)
            keep = []
            for pd in watch:
                if lo <= pd['p'] <= hi:
                    pd['catch'] = te - pd['t']
                    pd['catch_ts'] = d['ts'] - te
                elif te - pd['t'] > CW:
                    pd['catch'] = None
                else:
                    keep.append(pd)
            watch = keep
    cand.extend(pd for pd in pend if pd['t'] + W <= prev_te)
    for pd in watch:
        pd.setdefault('catch', None)
    rows = []
    for c in cand:
        if c['buy'] and c['p'] > c['hi_a']:
            kind = 'buy_выше_макс_аска'
            edge = c['hi_a']
            nlev = c['na']
            vol = c['va']
        elif (not c['buy']) and c['p'] < c['lo_b']:
            kind = 'sell_ниже_мин_бида'
            edge = c['lo_b']
            nlev = c['nb']
            vol = c['vb']
        else:
            kind = 'другая_сторона'
            edge = c['hi_a'] if c['p'] > c['hi_a'] else c['lo_b']
            nlev = c['na'] if c['p'] > c['hi_a'] else c['nb']
            vol = c['va'] if c['p'] > c['hi_a'] else c['vb']
        c['kind'] = kind
        c['dist_bp'] = abs(c['p'] - edge) / edge * 1e4
        c['nlev'] = nlev
        c['vol_ratio'] = c['size'] / vol if vol else 0.0
        c['span_bp'] = (c['hi_a'] - c['lo_a']) / c['lo_a'] * 1e4 if c['buy'] else (c['hi_b'] - c['lo_b']) / c['hi_b'] * 1e4
        rows.append(c)
    print('файл', sym, day, 'сделок', n, 'вне_глубины', len(rows))
    for k in ('buy_выше_макс_аска', 'sell_ниже_мин_бида', 'другая_сторона'):
        print(' ', k, sum(1 for r in rows if r['kind'] == k))
    if rows:
        print('  уровней на стороне: мин/мед/макс', min(r['nlev'] for r in rows), statistics.median(r['nlev'] for r in rows),
              max(r['nlev'] for r in rows))
        print('  глубина стороны, бп: мед', round(statistics.median(r['span_bp'] for r in rows), 1))
        ds = sorted(r['dist_bp'] for r in rows)
        print('  расстояние за край, бп: мед/q90/макс', round(ds[len(ds) // 2], 2), round(ds[int(len(ds) * .9)], 2), round(ds[-1], 2))
        vr = sorted(r['vol_ratio'] for r in rows)
        print('  размер сделки / объём стороны: мед/q90/макс', '%.2e %.2e %.2e' % (vr[len(vr) // 2], vr[int(len(vr) * .9)], vr[-1]))
        print('  цена уже была в книге раньше в файле:', sum(1 for r in rows if r['seen']), 'из', len(rows))
        print('  уровень удалён ранее (gone):', sum(1 for r in rows if r['gone'] is not None))
        cs = sorted(r['catch'] for r in rows if r.get('catch') is not None)
        print('  цена вернулась в окно книги ≤60 с:', len(cs), 'из', len(rows), '; до возврата, мс: мед/q90/макс',
              *( [round(cs[len(cs) // 2]), round(cs[int(len(cs) * .9)]), round(cs[-1])] if cs else ['-']))
        sec = {}
        for r in rows:
            sec[int(r['t'] // 1000)] = sec.get(int(r['t'] // 1000), 0) + 1
        top = sorted(sec.values(), reverse=True)
        print('  разных секунд с такими сделками:', len(sec), '; сделок в топ-10 секундах:', sum(top[:10]), 'из', len(rows))
        step = max(1, len(rows) // 12)
        ex = rows[::step][:12]
        with open(out, 'w') as f:
            f.write('t_мс\tp\tтейкер\tразмер\tвид\tнижн_бид\tверх_бид\tниж_аск\tверх_аск\tуровней_бид\tуровней_аск\tза_край_бп\tглубина_бп\tразмер/объём\tцена_была\n')
            for r in ex:
                f.write('%.0f\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%d\t%d\t%.2f\t%.1f\t%.2e\t%s\n' % (
                    r['t'], r['p'], 'buy' if r['buy'] else 'sell', r['size'], r['kind'], r['lo_b'], r['hi_b'], r['lo_a'], r['hi_a'],
                    r['nb'], r['na'], r['dist_bp'], r['span_bp'], r['vol_ratio'], r['seen']))


if __name__ == '__main__':
    if sys.argv[1] == 'file':
        main(sys.argv[2], sys.argv[3], sys.argv[4])
    else:
        sys.exit(__doc__)
