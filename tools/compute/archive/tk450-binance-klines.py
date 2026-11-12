#!/usr/bin/env python3
"""TK-450 (В-60): минутные свечи Binance USDⓈ-M с 2020-01 по 88 монетам. Подкоманды: map | dl | fill | verify | build [SYM…] | coverage.
Корень: /srv/alpha-sda/candles-1m/binance-um (raw/<SYM>/*.zip(+.CHECKSUM), <SYM>.csv.gz в формате Bybit/Gate).
Источник: data.binance.vision (листинг S3), потоков не больше 6, готовое не качается (.done-метка = файл + CHECKSUM сошлись)."""
import csv, gzip, hashlib, heapq, os, re, subprocess, sys, time, urllib.error, urllib.request, urllib.parse
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor

ROOT = os.environ.get('TK450_ROOT', '/srv/alpha-sda/candles-1m/binance-um')
RAW = f'{ROOT}/raw'
S3 = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision'
CDN = 'https://data.binance.vision/'
COINS = ('BTC ETH SOL ZEC XRP ENSO RLUSD UNI ARB BNB ENA U NEAR DOGE TRX LINK SUI DASH 0G PUMP PEPE TRUMP ADA HEMI CRV OP WLD '
         'TUT AAVE ONG LTC TAO AVAX XLM XPL BMT SC ONDO POL ACE INJ PROM TON ZRO FET APT BONK ASTER FIL DOT BCH ACT MORPHO ZEN '
         'HBAR PENGU FF TWT DOGS BICO CAKE SKY AR JST NOT ZKC ATM NFP ETHFI VIRTUAL WLFI DEXE SOMI GIGGLE ZK BERA SHIB ICP STRK '
         'TIA IQ LDO ONT PLUME PENDLE PYTH MITO STRAX').split()
MULT = ('', '1000', '1000000', '10000', '100000')
FIRST = '2020-01'
THREADS = 6


def get(url, tries=4, timeout=120):
    for i in range(tries):
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                return r.read()
        except Exception as e:
            if i == tries - 1 or (isinstance(e, urllib.error.HTTPError) and e.code == 404):
                raise
            time.sleep(2 * (i + 1))


def list_keys(prefix, delim=None):
    keys, marker = [], ''
    while True:
        q = {'prefix': prefix, 'max-keys': '1000', 'marker': marker}
        if delim:
            q['delimiter'] = delim
        x = get(S3 + '?' + urllib.parse.urlencode(q)).decode()
        keys += re.findall(r'<Key>([^<]+)</Key>', x) if not delim else re.findall(r'<Prefix>([^<]+)</Prefix>', x)[1:]
        if '<IsTruncated>true' not in x:
            return keys
        m = re.search(r'<NextMarker>([^<]+)</NextMarker>', x)
        marker = m.group(1) if m else keys[-1]


def cmd_map():
    os.makedirs(ROOT, exist_ok=True)
    syms = {p.split('/')[-2] for p in list_keys('data/futures/um/monthly/klines/', '/')}
    daily = {p.split('/')[-2] for p in list_keys('data/futures/um/daily/klines/', '/')}
    rows, files = [], []
    for c in COINS:
        cand = [m + c + 'USDT' for m in MULT if m + c + 'USDT' in syms | daily]
        if not cand:
            cand = [m + c + 'USDC' for m in MULT if m + c + 'USDC' in syms | daily]
        if not cand:
            rows.append([c, '', '', '', 'нет USDⓈ-M контракта на data.binance.vision (ни USDT, ни USDC, ни с множителем)'])
            continue
        for s in cand:
            mk = [k for k in list_keys(f'data/futures/um/monthly/klines/{s}/1m/') if k.endswith('.zip')]
            mk = [k for k in mk if re.search(r'-(\d{4}-\d\d)\.zip$', k).group(1) >= FIRST]
            months = {re.search(r'-(\d{4}-\d\d)\.zip$', k).group(1) for k in mk}
            dk = [k for k in list_keys(f'data/futures/um/daily/klines/{s}/1m/') if k.endswith('.zip')]
            dk = [k for k in dk if re.search(r'-(\d{4}-\d\d)-\d\d\.zip$', k).group(1) not in months
                  and re.search(r'-(\d{4}-\d\d)-\d\d\.zip$', k).group(1) >= FIRST]
            allk = sorted(mk + dk, key=os.path.basename)
            if not allk:
                rows.append([c, s, '', '', 'символ есть, файлов 1m с 2020-01 нет'])
                continue
            first = re.search(r'-1m-(\d{4}-\d\d)', allk[0]).group(1)
            last = re.search(r'-1m-(\d{4}-\d\d(?:-\d\d)?)', allk[-1]).group(1)
            rows.append([c, s, first, last, f'месячных {len(mk)}, дневных {len(dk)}' + ('; множитель в имени' if s != c + 'USDT' else '')])
            files += allk
    with open(f'{ROOT}/map.csv', 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['монета', 'символ', 'первый_месяц', 'последний_файл', 'примечание']); w.writerows(rows)
    with open(f'{ROOT}/files.lst', 'w') as f:
        f.write('\n'.join(files) + '\n')
    print('coins', len(COINS), 'symbols', len({r[1] for r in rows if r[1]}), 'files', len(files))


def progress(done, total, step):
    subprocess.run(['bash', max((os.path.join(os.path.dirname(os.path.abspath(__file__)), p) for p in ('progress.sh', '../progress.sh')), key=os.path.exists), 'tk450', 'TK-450', step, str(done), str(total), 'файлов'],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)


def one_dl(key):
    sym = key.split('/')[-3]
    d = f'{RAW}/{sym}'; os.makedirs(d, exist_ok=True)
    z = f'{d}/{os.path.basename(key)}'
    if os.path.exists(z + '.done'):
        return 0
    for _ in range(3):
        data, chk = get(CDN + key, timeout=300), get(CDN + key + '.CHECKSUM').decode().split()[0]
        if hashlib.sha256(data).hexdigest() == chk:
            with open(z, 'wb') as f: f.write(data)
            with open(z + '.CHECKSUM', 'w') as f: f.write(chk + '\n')
            open(z + '.done', 'w').close()
            return len(data)
    with open(f'{ROOT}/dl-fail.txt', 'a') as f: f.write(key + '\n')
    return 0


def cmd_dl():
    keys = [k for k in open(f'{ROOT}/files.lst').read().split('\n') if k]
    done = nbytes = 0
    t0 = time.time()
    with ThreadPoolExecutor(THREADS) as ex:
        for b in ex.map(one_dl, keys):
            done += 1; nbytes += b
            if done % 25 == 0 or done == len(keys):
                progress(done, len(keys), 'скачивание')
                print(f'{done}/{len(keys)} {nbytes / 1e6:.0f} МБ {time.time() - t0:.0f} с', flush=True)


def cmd_verify():
    bad = n = 0
    with open(f'{ROOT}/verify.csv', 'w', newline='') as out:
        w = csv.writer(out); w.writerow(['файл', 'sha256', 'checksum', 'ok'])
        for sym in sorted(os.listdir(RAW)):
            for fn in sorted(os.listdir(f'{RAW}/{sym}')):
                if not fn.endswith('.zip'):
                    continue
                p = f'{RAW}/{sym}/{fn}'
                h = hashlib.sha256(open(p, 'rb').read()).hexdigest()
                c = open(p + '.CHECKSUM').read().split()[0]
                n += 1; bad += h != c
                w.writerow([f'{sym}/{fn}', h, c, int(h == c)])
    print('files', n, 'mismatch', bad)


def _rows(path):
    import zipfile
    with zipfile.ZipFile(path) as zf:
        for line in zf.open(zf.namelist()[0]):
            p = line.rstrip().split(b',')
            if not p[0][:1].isdigit():
                continue  # шапка
            t = int(p[0])
            if t > 10**14:
                t //= 1000  # микросекунды → мс
            yield t, b'%d,%s,%s,%s,%s,%s,%s,%s\n' % (t, p[1], p[2], p[3], p[4], p[5], p[7], p[8])


def build_one(sym):
    """Слияние месячных и дневных архивов по времени: дневной файл закрывает дыры месячного; совпавшая минута должна совпасть байт в байт."""
    d = f'{RAW}/{sym}'
    zs = sorted(f for f in os.listdir(d) if f.endswith('.zip'))
    out, rows, prev, prev_line = f'{ROOT}/{sym}.csv.gz', 0, None, None
    tmp = out + '.tmp'
    with gzip.open(tmp, 'wb', 6) as g:
        g.write(b't_open_ms,o,h,l,c,v,quote_v,n_trades' + chr(10).encode())
        for t, line in heapq.merge(*[_rows(f'{d}/{z}') for z in zs]):
            if prev is not None and t == prev:
                if line != prev_line:
                    raise SystemExit(f'{sym}: минута {t} в двух файлах с разными значениями')
                continue
            if prev is not None and t < prev:
                raise SystemExit(f'{sym}: время не растёт: {t} после {prev}')
            prev, prev_line = t, line; rows += 1
            g.write(line)
    os.replace(tmp, out)
    return sym, rows


def cmd_build():
    syms = sys.argv[2:] or sorted({r['символ'] for r in csv.DictReader(open(f'{ROOT}/map.csv')) if r['символ']})
    with ProcessPoolExecutor(6) as ex:
        for r in ex.map(build_one, syms):
            print(r, flush=True)


def scan(sym):
    rows, prev, t0, gaps = 0, None, None, []
    with gzip.open(f'{ROOT}/{sym}.csv.gz', 'rb') as g:
        next(g)
        for line in g:
            t = int(line.split(b',', 1)[0])
            if prev is not None and t > prev + 60000:
                gaps.append((prev + 60000, (t - prev) // 60000 - 1))
            t0 = t0 or t; prev = t; rows += 1
    return sym, rows, t0, prev, gaps


def scan_all():
    mp = list(csv.DictReader(open(f'{ROOT}/map.csv')))
    syms = sorted({r['символ'] for r in mp if r['символ']})
    with ProcessPoolExecutor(6) as ex:
        return mp, {r[0]: r for r in ex.map(scan, syms)}


def write_gaps(path, res):
    with open(path, 'w', newline='') as f:
        w = csv.writer(f); w.writerow(['символ', 'начало_мс', 'начало_utc', 'минут'])
        for s, r in sorted(res.items()):
            for t, n in r[4]:
                w.writerow([s, t, time.strftime('%Y-%m-%d %H:%M', time.gmtime(t / 1000)), n])


def cmd_fill():
    """Дыры собранных свечей → дневные архивы тех суток (в месячных файлах Binance этих минут нет); нет и дневного — остаётся дырой."""
    _, res = scan_all()
    write_gaps(f'{ROOT}/gaps-before-fill.csv', res)
    keys, aff = [], set()
    for s, r in res.items():
        days = {time.strftime('%Y-%m-%d', time.gmtime(t / 1000)) for st, n in r[4] for t in range(st, st + n * 60000, 60000)}
        for d in sorted(days):
            k = f'data/futures/um/daily/klines/{s}/1m/{s}-1m-{d}.zip'
            if not os.path.exists(f'{RAW}/{s}/{os.path.basename(k)}.done'):
                keys.append(k); aff.add(s)

    def one(k):
        try:
            return k, one_dl(k)
        except urllib.error.HTTPError as e:
            return k, -e.code
    got = miss = 0
    with ThreadPoolExecutor(THREADS) as ex, open(f'{ROOT}/fill-miss.txt', 'w') as fm:
        for k, b in ex.map(one, keys):
            if b > 0: got += 1
            else: miss += 1; fm.write(f'{k} {b}' + chr(10))
    with open(f'{ROOT}/files-fill.lst', 'w') as f: f.write(chr(10).join(keys) + chr(10))
    with open(f'{ROOT}/fill-affected.txt', 'w') as f: f.write(' '.join(sorted(aff)) + chr(10))
    print('дыр-суток', len(keys), 'скачано', got, 'нет в архиве/ошибка', miss, 'монет', len(aff))


def cmd_coverage():
    mp, res = scan_all()
    write_gaps(f'{ROOT}/gaps.csv', res)
    with open(f'{ROOT}/coverage.csv', 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['монета', 'символ', 'первый_месяц', 'минут', 'пропусков_мин', 'причина', 'первая_мс', 'последняя_мс', 'файлов_zip'])
        for r in mp:
            s = r['символ']
            if not s:
                w.writerow([r['монета'], '', '', 0, '', r['примечание'], '', '', 0]); continue
            _, rows, t0, t1, gaps = res[s]
            gm = sum(n for _, n in gaps)
            nz = sum(fn.endswith('.zip') for fn in os.listdir(f'{RAW}/{s}'))
            w.writerow([r['монета'], s, time.strftime('%Y-%m', time.gmtime(t0 / 1000)), rows, gm,
                        r['примечание'] + (f'; пропуски: {len(gaps)} интервалов (gaps.csv) — минут нет в файлах архива, не выдумывались' if gaps else ''),
                        t0, t1, nz])


if __name__ == '__main__':
    {'map': cmd_map, 'dl': cmd_dl, 'fill': cmd_fill, 'verify': cmd_verify, 'build': cmd_build, 'coverage': cmd_coverage}[sys.argv[1]]()
