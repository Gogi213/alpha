#!/usr/bin/env python3
"""TK-478: история ставок фандинга Binance (USDT-M) и Bybit (linear) для вселенной TK-476 (union по истории, с делистнутыми).
usage: tk478-funding.py bybit|binance|cov <OUT> <UNIVERSE_DIR> [threads]
OUT/{binance-um,bybit-linear}/<SYM>.csv.gz: time_ms,rate,interval_h. Bybit: ставка с REST /v5/market/funding/history (назад от
now по endTime, 200 записей), interval_h = разрыв до ПРЕДЫДУЩЕЙ записи в часах (первая запись — до следующей): REST не отдаёт
интервал, фактический читается по самим меткам. Binance: месячные zip data.binance.vision fundingRate (interval_h из файла, если
колонка есть; иначе разрыв), кэш TK-455 (--cache) не качается заново; хвост после последнего месячного архива — REST
/fapi/v1/fundingRate (interval_h = разрыв). Рестарт пропускает готовые <SYM>.csv.gz. cov — OUT/coverage-<биржа>.csv."""
import csv, gzip, io, json, os, re, sys, threading, time, urllib.parse, urllib.request, urllib.error, zipfile
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

UA = {'User-Agent': 'alpha-tk478'}
BN_S3 = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/'
CACHE = os.environ.get('TK478_CACHE', '/data/tk0455/fund')
HOLE_H = 8.0  # максимальный интервал фандинга у обеих бирж
REST_GAP = 0.8  # с между REST-запросами Binance на весь процесс (лимит ~500/5 мин на IP)
_rl, _rt = threading.Lock(), [0.0]


def rest_wait():
    with _rl:
        d = _rt[0] + REST_GAP - time.time()
        if d > 0:
            time.sleep(d)
        _rt[0] = time.time()


def rd(url, tries=8, raw=False, pause=1.0):
    url = urllib.parse.quote(url, safe=":/?&=%")
    err = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                b = r.read()
                return b if raw else json.loads(b)
        except urllib.error.HTTPError as e:
            err = e
            if e.code == 404:
                return None
            time.sleep(pause * (2 ** min(i, 5)) * (6 if e.code in (418, 429) else 1))
        except Exception as e:
            err = e
            time.sleep(pause * (2 ** min(i, 4)))
    raise RuntimeError(f'{url}: {err}')


def write_rows(path, rows):
    """rows: [(ts_ms, rate_str, interval_h or None)] -> csv.gz атомарно; interval_h пустой -> разрыв."""
    rows = sorted(set((t, r, h) for t, r, h in rows), key=lambda x: x[0])
    out, seen = [], set()
    for t, r, h in rows:
        if t in seen:
            continue
        seen.add(t)
        out.append([t, r, h])
    for i, x in enumerate(out):
        if x[2] is None:
            if i > 0:
                x[2] = round((x[0] - out[i - 1][0]) / 3.6e6, 4)
            elif len(out) > 1:
                x[2] = round((out[1][0] - x[0]) / 3.6e6, 4)
            else:
                x[2] = ''
    tmp = path + '.part'
    with gzip.open(tmp, 'wt', newline='') as f:
        w = csv.writer(f)
        w.writerow(['time_ms', 'rate', 'interval_h'])
        w.writerows(out)
    os.replace(tmp, path)
    return len(out)


def symbols(udir, ex):
    with open(os.path.join(udir, f'union-{ex}.csv')) as f:
        return sorted({r['symbol'] for r in csv.DictReader(f)})


def bybit_one(sym, out):
    p = os.path.join(out, 'bybit-linear', sym + '.csv.gz')
    if os.path.exists(p):
        return sym, -1, 0
    rows, end, nreq = [], int(time.time() * 1000), 0
    while True:
        d = rd(f'https://api.bybit.com/v5/market/funding/history?category=linear&symbol={sym}&limit=200&endTime={end}')
        nreq += 1
        if d is None or d.get('retCode') != 0:
            raise RuntimeError(f'{sym}: {d}')
        lst = d['result']['list']
        if not lst:
            break
        rows += [(int(x['fundingRateTimestamp']), x['fundingRate'], None) for x in lst]
        mn = min(int(x['fundingRateTimestamp']) for x in lst)
        if len(lst) < 200:
            break
        end = mn - 1
    return sym, write_rows(p, rows), nreq


def parse_zip(b):
    z = zipfile.ZipFile(io.BytesIO(b))
    rows = []
    for n in z.namelist():
        rd_ = csv.reader(io.TextIOWrapper(z.open(n), 'utf-8'))
        head = next(rd_)
        ci = {h: i for i, h in enumerate(head)}
        for r in rd_:
            if not r or not r[0].isdigit():
                continue
            h = r[ci['funding_interval_hours']] if 'funding_interval_hours' in ci else ''
            rows.append((int(r[ci['calc_time']]), r[ci['last_funding_rate']], float(h) if h not in ('', None) else None))
    return rows


def binance_one(sym, out):
    p = os.path.join(out, 'binance-um', sym + '.csv.gz')
    if os.path.exists(p):
        return sym, -1, 0
    months, tok, nreq = {}, '', 0
    while True:
        u = f'{BN_S3}?list-type=2&prefix=data/futures/um/monthly/fundingRate/{sym}/' + (f'&continuation-token={urllib.parse.quote(tok)}' if tok else '')
        xml = rd(u, raw=True).decode('utf-8', 'replace')
        nreq += 1
        for k in re.findall(r'<Key>([^<]+\.zip)</Key>', xml):
            m = re.search(r'/' + re.escape(sym) + r'-fundingRate-(\d{4}-\d{2})\.zip$', k)
            if m:
                months[m.group(1)] = k
        mt = re.search(r'<NextContinuationToken>([^<]+)</NextContinuationToken>', xml)
        if not mt:
            break
        tok = mt.group(1)
    rows = []
    for mo in sorted(months):
        cp = os.path.join(CACHE, f'{sym}-fundingRate-{mo}.zip')
        if os.path.exists(cp):
            b = open(cp, 'rb').read()
        else:
            b = rd(BN_S3 + months[mo], raw=True)
            nreq += 1
        rows += parse_zip(b)
    # хвост после последнего месячного архива: REST, интервал = разрыв
    last = max(r[0] for r in rows) if rows else 0
    start = last + 1 if rows else 1569888000000  # 2019-10-01: запуск USDT-M
    fetch = not rows or last > time.time() * 1000 - 62 * 86400000  # давно делистнутые: хвоста нет, запрос не нужен
    while fetch:
        rest_wait()
        d = rd(f'https://fapi.binance.com/fapi/v1/fundingRate?symbol={sym}&startTime={start}&limit=1000', pause=3.0)
        nreq += 1
        if not d:
            break
        rows += [(int(x['fundingTime']), x['fundingRate'], None) for x in d]
        if len(d) < 1000:
            break
        start = max(int(x['fundingTime']) for x in d) + 1
    # REST-часть без interval_h: write_rows считает разрыв по соседям; из архива interval_h брали из файла
    return sym, write_rows(p, rows), nreq


def cov(out, ex):
    d = os.path.join(out, ex)
    res = []
    for fn in sorted(os.listdir(d)):
        if not fn.endswith('.csv.gz'):
            continue
        ts = []
        with gzip.open(os.path.join(d, fn), 'rt') as f:
            r = csv.reader(f); next(r)
            ts = [int(x[0]) for x in r]
        holes, miss, mx = 0, 0.0, 0.0
        for a, b in zip(ts, ts[1:]):
            g = (b - a) / 3.6e6
            if g > HOLE_H + 0.01:
                holes += 1; miss += g; mx = max(mx, g)
        iso = lambda t: datetime.fromtimestamp(t / 1000, timezone.utc).strftime('%Y-%m-%d %H:%M')
        res.append([fn[:-7], iso(ts[0]) if ts else '', iso(ts[-1]) if ts else '', len(ts), holes, round(miss, 1), round(mx, 1)])
    with open(os.path.join(out, f'coverage-{ex}.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['symbol', 'first', 'last', 'n', 'holes_gt8h', 'hole_hours', 'max_gap_h'])
        w.writerows(res)
    print(ex, len(res), 'files', sum(r[3] for r in res), 'records', sum(1 for r in res if r[4]), 'with holes', flush=True)


def main():
    mode, out, udir = sys.argv[1], sys.argv[2], sys.argv[3]
    th = int(sys.argv[4]) if len(sys.argv) > 4 else 8
    if mode == 'cov':
        for ex in ('binance-um', 'bybit-linear'):
            if os.path.isdir(os.path.join(out, ex)):
                cov(out, ex)
        return
    ex, fn = ('bybit-linear', bybit_one) if mode == 'bybit' else ('binance-um', binance_one)
    os.makedirs(os.path.join(out, ex), exist_ok=True)
    syms = symbols(udir, ex)
    t0, done, req, fail = time.time(), 0, 0, []
    with ThreadPoolExecutor(th) as pool:
        futs = {pool.submit(fn, s, out): s for s in syms}
        for fu in futs:
            s = futs[fu]
            try:
                _, n, q = fu.result()
                done += 1; req += q
            except Exception as e:
                fail.append(s); print('FAIL', s, e, flush=True)
            if done % 50 == 0:
                print(f'{mode} {done}/{len(syms)} req={req} {time.time()-t0:.0f}s', flush=True)
    print(f'{mode} DONE {done}/{len(syms)} fail={len(fail)} req={req} {time.time()-t0:.0f}s', flush=True)
    sys.exit(1 if fail else 0)


main()
