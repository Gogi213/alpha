#!/usr/bin/env python3
"""TK-476 (В-85): минутные свечи Binance USDⓈ-M для монет пула (> 5 млн/сут), которых ещё нет в binance-um. Использует функции tk450.
usage: tk476-binance-new.py <список_символов.txt>   (map → dl → verify → build → coverage только по этим символам; существующие файлы не трогает)
Выход: $ROOT/map-476.csv, verify-476.csv, coverage-476.csv, gaps-476.csv; файлы <SYM>.csv.gz в $ROOT."""
import csv, hashlib, importlib.util, os, re, sys, time, urllib.parse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

spec = importlib.util.spec_from_file_location('k450', os.environ.get('TK450_PY', '/data/tk450/tk450-binance-klines.py'))
k = importlib.util.module_from_spec(spec); spec.loader.exec_module(k)
ROOT, RAW = k.ROOT, k.RAW
_get = k.get


def _qget(url, *a, **kw):  # символы с не-ASCII (кириллица/иероглифы) в ключе S3 — иначе UnicodeEncodeError
    return _get(urllib.parse.quote(url, safe=":/?&=%"), *a, **kw)


k.get = _qget
FIRST = k.FIRST


def keys_for(sym):
    mk = [x for x in k.list_keys(f'data/futures/um/monthly/klines/{sym}/1m/') if x.endswith('.zip')]
    mk = [x for x in mk if re.search(r'-(\d{4}-\d\d)\.zip$', x).group(1) >= FIRST]
    months = {re.search(r'-(\d{4}-\d\d)\.zip$', x).group(1) for x in mk}
    dk = [x for x in k.list_keys(f'data/futures/um/daily/klines/{sym}/1m/') if x.endswith('.zip')]
    dk = [x for x in dk if re.search(r'-(\d{4}-\d\d)-\d\d\.zip$', x).group(1) not in months
          and re.search(r'-(\d{4}-\d\d)-\d\d\.zip$', x).group(1) >= FIRST]
    return sorted(mk + dk, key=os.path.basename), len(mk), len(dk)


def main():
    syms = [s.strip() for s in open(sys.argv[1]) if s.strip()]
    os.makedirs(RAW, exist_ok=True)
    rows, files = [], []
    with ThreadPoolExecutor(4) as ex:
        for s, (allk, nm, nd) in zip(syms, ex.map(keys_for, syms)):
            if not allk:
                rows.append([s, '', '', 'файлов 1m с 2020-01 на data.binance.vision нет']); continue
            first = re.search(r'-1m-(\d{4}-\d\d)', allk[0]).group(1)
            last = re.search(r'-1m-(\d{4}-\d\d(?:-\d\d)?)', allk[-1]).group(1)
            rows.append([s, first, last, f'месячных {nm}, дневных {nd}']); files += allk
    with open(f'{ROOT}/map-476.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['символ', 'первый_месяц', 'последний_файл', 'примечание']); w.writerows(rows)
    print('symbols', len(syms), 'with files', sum(1 for r in rows if r[1]), 'files', len(files), flush=True)
    done = nb = 0; t0 = time.time()
    with ThreadPoolExecutor(k.THREADS) as ex:
        for b in ex.map(k.one_dl, files):
            done += 1; nb += b
            if done % 100 == 0 or done == len(files):
                print(f'dl {done}/{len(files)} {nb / 1e6:.0f} МБ {time.time() - t0:.0f} с', flush=True)
    # verify только по своим символам
    bad = n = 0
    with open(f'{ROOT}/verify-476.csv', 'w', newline='') as out:
        w = csv.writer(out); w.writerow(['файл', 'sha256', 'checksum', 'ok'])
        for r in rows:
            s = r[0]
            if not r[1]: continue
            for fn in sorted(os.listdir(f'{RAW}/{s}')):
                if not fn.endswith('.zip'): continue
                p = f'{RAW}/{s}/{fn}'
                h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
                c = open(p + '.CHECKSUM').read().split()[0]
                n += 1; bad += h != c
                w.writerow([f'{s}/{fn}', h, c, int(h == c)])
    print('verify files', n, 'mismatch', bad, flush=True)
    if bad: raise SystemExit('verify: расхождения sha256')
    have = [r[0] for r in rows if r[1]]
    with ProcessPoolExecutor(6) as ex:
        for r in ex.map(k.build_one, have): print('build', r, flush=True)
    with ProcessPoolExecutor(6) as ex:
        res = {r[0]: r for r in ex.map(k.scan, have)}
    k.write_gaps(f'{ROOT}/gaps-476.csv', res)
    with open(f'{ROOT}/coverage-476.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['символ', 'первый_месяц', 'минут', 'пропусков_мин', 'причина', 'первая_мс', 'последняя_мс', 'файлов_zip'])
        for r in rows:
            s = r[0]
            if not r[1]:
                w.writerow([s, '', 0, '', r[3], '', '', 0]); continue
            _, nrows, t0_, t1, gaps = res[s]
            gm = sum(g for _, g in gaps)
            nz = sum(fn.endswith('.zip') for fn in os.listdir(f'{RAW}/{s}'))
            w.writerow([s, time.strftime('%Y-%m', time.gmtime(t0_ / 1000)), nrows, gm,
                        r[3] + (f'; пропуски: {len(gaps)} интервалов (gaps-476.csv) — минут нет в файлах архива, не выдумывались' if gaps else ''),
                        t0_, t1, nz])
    print('done', flush=True)


if __name__ == '__main__':
    main()
