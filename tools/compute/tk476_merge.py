#!/usr/bin/env python3
"""TK-476: слияние стадии (candles-1m-476/<биржа>/<SYM>.csv.gz, с 2020) с лежащими (candles-1m/<биржа>/<SYM>.csv.gz, текущие месяцы).
Сверка по перекрытию: на общих минутах t_open_ms o,h,l,c,v,quote_v совпадают (отн. 1e-9), n_trades — равны как строки. Расхождений 0 →
итоговый файл = стадия ∪ минуты, которых нет в стадии (прежний уходит в <STAGE>/prev/<биржа>/); иначе лежащий НЕ трогаем, символ в
overlap-<биржа>.csv помечен «расхождение». Нового символа (нет лежащего) — просто перенос. Минут не выдумывает.
usage: tk476_merge.py <биржа> [--apply] [--only SYM,SYM]   (без --apply — только сверка, файлы не меняются)"""
import csv, gzip, math, os, shutil, sys

ROOT, STAGE = '/srv/alpha-sda/candles-1m', '/srv/alpha-sda/candles-1m-476'
HDR = 't_open_ms,o,h,l,c,v,quote_v,n_trades\n'


def same(a, b):
    if a[7].strip() != b[7].strip():
        return False
    return all(math.isclose(float(a[i]), float(b[i]), rel_tol=1e-9, abs_tol=1e-12) for i in range(1, 7))


def one(ex, sym, apply):
    st, cur = f'{STAGE}/{ex}/{sym}.csv.gz', f'{ROOT}/{ex}/{sym}.csv.gz'
    if not os.path.exists(cur):
        if apply:
            shutil.move(st, cur)
        return [sym, 0, 0, 0, 'новый символ: перенос' if apply else 'новый символ']
    old = {}
    with gzip.open(cur, 'rt') as f:
        next(f)
        for l in f:
            p = l.rstrip('\n').split(',')
            old[p[0]] = p
    seen = mism = 0
    lo = min(old, key=int) if old else None
    tmp = st + '.merge.tmp'
    with gzip.open(st, 'rt') as f, gzip.open(tmp, 'wt', compresslevel=6) as g:
        g.write(HDR); next(f)
        for l in f:
            t = l.split(',', 1)[0]
            o = old.get(t)
            if o is not None:
                seen += 1
                mism += not same(l.rstrip('\n').split(','), o)
            g.write(l)
    extra = len(old) - seen  # минуты лежащего, которых нет в стадии
    act = 'сверка'
    if mism == 0 and apply:
        if extra:  # редко: вставить недостающие минуты лежащего в порядке времени
            rows = {}
            with gzip.open(tmp, 'rt') as f:
                next(f)
                for l in f: rows[l.split(',', 1)[0]] = l
            for t, p in old.items(): rows.setdefault(t, ','.join(p) + '\n')
            with gzip.open(tmp, 'wt', compresslevel=6) as g:
                g.write(HDR)
                for t in sorted(rows, key=int): g.write(rows[t])
        os.makedirs(f'{STAGE}/prev/{ex}', exist_ok=True)
        shutil.copy2(cur, f'{STAGE}/prev/{ex}/{sym}.csv.gz')
        os.replace(tmp, cur); os.remove(st)
        act = 'слито' + (f' (+{extra} мин лежащего)' if extra else '')
    else:
        os.remove(tmp)
        if mism: act = 'РАСХОЖДЕНИЕ: лежащий не тронут'
    return [sym, seen, mism, extra, act]


def main():
    ex, apply = sys.argv[1], '--apply' in sys.argv
    only = sys.argv[sys.argv.index('--only') + 1].split(',') if '--only' in sys.argv else None
    syms = sorted(f[:-7] for f in os.listdir(f'{STAGE}/{ex}') if f.endswith('.csv.gz') and (not only or f[:-7] in only))
    with open(f'{STAGE}/overlap-{ex}.csv', 'w', newline='') as o:
        w = csv.writer(o); w.writerow(['символ', 'общих_минут', 'расхождений', 'минут_только_в_лежащем', 'действие'])
        for s in syms:
            r = one(ex, s, apply); w.writerow(r); print(*r, flush=True)


if __name__ == '__main__':
    main()
