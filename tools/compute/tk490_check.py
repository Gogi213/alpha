#!/usr/bin/env python3
"""TK-490: проверка скачанных файлов пампов — свечи 1 м и ставки фандинга по списку контрактов вселенной.
usage: tk490_check.py candles <каталог> <symbols.txt> <out.csv>   (каждый <SYM>.csv.gz: t_open_ms,o,h,l,c,v,quote_v,n_trades)
       tk490_check.py funding <каталог> <symbols.txt> <out.csv>   (каждый <SYM>.csv.gz: time_ms,rate,interval_h)
       tk490_check.py --selftest
out.csv — строка на контракт: symbol,rows,first,last,dups,unordered,off_minute,missing_min,gaps,max_gap_min,status
(для фандинга: symbol,rows,first,last,dups,unordered,days,rows_per_day,min_interval_h,max_interval_h,status).
status: ok | missing_file | empty | dups | unordered | off_minute. Дыры не считаются ошибкой (зеркало биржи, В-172) — это таблица.
Итог в stdout: файлов / контрактов, суммы; код 1, если есть missing_file, empty, dups, unordered или off_minute."""
import csv, gzip, os, sys, tempfile
from datetime import datetime, timezone

MIN = 60000
BAD = ('missing_file', 'empty', 'dups', 'unordered', 'off_minute')


def iso(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M') if ms is not None else ''


def candles_one(path):
    rows = dups = unordered = off = missing = gaps = mx = 0
    first = prev = None
    with gzip.open(path, 'rt') as f:
        r = csv.reader(f)
        next(r, None)
        for p in r:
            if not p or not p[0].lstrip('-').isdigit():
                continue
            t = int(p[0])
            rows += 1
            off += t % MIN != 0
            if first is None:
                first = t
            elif t == prev:
                dups += 1
            elif t < prev:
                unordered += 1
            elif t > prev + MIN:
                g = (t - prev) // MIN - 1
                missing += g
                gaps += 1
                mx = max(mx, g)
            if prev is None or t > prev:
                prev = t
    status = 'empty' if rows == 0 else 'dups' if dups else 'unordered' if unordered else 'off_minute' if off else 'ok'
    return [rows, iso(first), iso(prev), dups, unordered, off, missing, gaps, mx, status]


def funding_one(path):
    rows = dups = unordered = 0
    first = prev = None
    ivs = []
    with gzip.open(path, 'rt') as f:
        r = csv.reader(f)
        next(r, None)
        for p in r:
            if not p or not p[0].lstrip('-').isdigit():
                continue
            t = int(p[0])
            rows += 1
            if first is None:
                first = t
            elif t == prev:
                dups += 1
            elif t < prev:
                unordered += 1
            if len(p) > 2 and p[2] not in ('', None):
                ivs.append(float(p[2]))
            if prev is None or t > prev:
                prev = t
    span = (prev - first) / 86400000 if rows > 1 else 0
    days = round(span, 2)
    status = 'empty' if rows == 0 else 'dups' if dups else 'unordered' if unordered else 'ok'
    return [rows, iso(first), iso(prev), dups, unordered, days, round((rows - 1) / span, 3) if span else '',
            min(ivs) if ivs else '', max(ivs) if ivs else '', status]


def run(kind, d, symfile, out):
    syms = [s.strip() for s in open(symfile) if s.strip()]
    one = candles_one if kind == 'candles' else funding_one
    head = (['symbol', 'rows', 'first', 'last', 'dups', 'unordered', 'off_minute', 'missing_min', 'gaps', 'max_gap_min', 'status']
            if kind == 'candles' else
            ['symbol', 'rows', 'first', 'last', 'dups', 'unordered', 'days', 'rows_per_day', 'min_interval_h', 'max_interval_h', 'status'])
    res = []
    for s in syms:
        p = os.path.join(d, s + '.csv.gz')
        res.append([s, 0, '', '', 0, 0, 0, 0, 0, 0, 'missing_file'][:len(head)] if not os.path.exists(p) else [s] + one(p))
    with open(out, 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(head)
        w.writerows(res)
    st = {}
    for r in res:
        st[r[-1]] = st.get(r[-1], 0) + 1
    print(kind, 'контрактов', len(syms), 'файлов', sum(r[-1] != 'missing_file' for r in res), 'строк', sum(r[1] for r in res), st)
    return 1 if any(k in st for k in BAD) else 0


def selftest():
    tmp = tempfile.mkdtemp()

    def w(name, lines, head):
        with gzip.open(os.path.join(tmp, name + '.csv.gz'), 'wt') as f:
            f.write(head + '\n' + ''.join(l + '\n' for l in lines))
    h = 't_open_ms,o,h,l,c,v,quote_v,n_trades'
    row = lambda t: f'{t},1,1,1,1,1,1,'
    base = 1735689600000
    w('OK', [row(base + i * MIN) for i in (0, 1, 2, 5, 6)], h)  # дыра 2 минуты
    w('DUP', [row(base), row(base + MIN), row(base + MIN)], h)
    w('ORD', [row(base), row(base + 2 * MIN), row(base + MIN)], h)
    w('OFF', [row(base), row(base + MIN + 1)], h)
    w('EMP', [], h)
    open(os.path.join(tmp, 's.txt'), 'w').write('OK\nDUP\nORD\nOFF\nEMP\nGONE\n')
    out = os.path.join(tmp, 'o.csv')
    assert run('candles', tmp, os.path.join(tmp, 's.txt'), out) == 1
    got = {r['symbol']: r for r in csv.DictReader(open(out))}
    assert got['OK']['status'] == 'ok' and got['OK']['missing_min'] == '2' and got['OK']['gaps'] == '1', got['OK']
    assert got['DUP']['status'] == 'dups' and got['ORD']['status'] == 'unordered' and got['OFF']['status'] == 'off_minute'
    assert got['EMP']['status'] == 'empty' and got['GONE']['status'] == 'missing_file'
    fh = 'time_ms,rate,interval_h'
    w('F1', [f'{base + i * 8 * 3600000},0.0001,8' for i in range(6)], fh)
    w('F2', [f'{base},0.0001,8', f'{base},0.0001,8'], fh)
    open(os.path.join(tmp, 'f.txt'), 'w').write('F1\nF2\n')
    assert run('funding', tmp, os.path.join(tmp, 'f.txt'), out) == 1
    got = {r['symbol']: r for r in csv.DictReader(open(out))}
    assert got['F1']['status'] == 'ok' and got['F1']['rows_per_day'] == '3.0' and got['F2']['status'] == 'dups', got
    print('selftest ok')


if __name__ == '__main__':
    if sys.argv[1:] == ['--selftest']:
        selftest()
    else:
        sys.exit(run(*sys.argv[1:5]))
