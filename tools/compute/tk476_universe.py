#!/usr/bin/env python3
"""TK-476 (В-85, поправка владельца 01.11): динамическая вселенная монет по истории, не снимок.
Для каждой биржи — суточный оборот (quote, USDT) по 1d-свечам биржи с 2020-01-01 для ВСЕХ перпетуалов USDT, в том числе
делистнутых: Binance — REST (текущие по exchangeInfo) + месячные 1d-архивы data.binance.vision (кого нет в exchangeInfo);
Bybit — instruments-info Trading+Closed+Delivering, kline D; Gate — имена из листинга месячных архивов
gateio-public-data (futures_usdt/trades/ГГГГММ/) и 1d-свечи API (отдаёт и делистнутые).
Выход в <out>/: turnover-<биржа>.csv.gz (exchange,symbol,day,turnover_usdt,med30_prev,n30_prev) — med30_prev = медиана оборота
по дням ОКНА D-30..D-1, где свеча была (дней без свечи не выдумываем), n30_prev — сколько дней в окне (point-in-time, без
заглядывания); universe-<биржа>.csv (exchange,symbol,from_day,to_day — включительно, подряд идущие сутки со свечой и
med30_prev >= порога, n30_prev >= 1); union.csv (монета проходит, если ХОТЬ В ОДНОМ календарном месяце медиана суточного
оборота месяца >= порога); sources.csv (откуда взяты свечи, чего нет). Пороги: Binance 5 млн, Bybit 1 млн, Gate 1 млн.
usage: tk476_universe.py <out> [--only binance-um,bybit-linear,gate-futures_usdt]"""
import csv, gzip, io, json, os, re, statistics, sys, time, urllib.parse, urllib.request, zipfile
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

TH = {'binance-um': 5e6, 'bybit-linear': 1e6, 'gate-futures_usdt': 1e6}
START = date(2020, 1, 1)
UA = {'User-Agent': 'alpha-tk476'}
BN_S3 = 'https://s3-ap-northeast-1.amazonaws.com/data.binance.vision/'


def rd(url, tries=6, raw=False):
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                b = r.read()
            return b if raw else json.loads(b)
        except urllib.error.HTTPError as e:
            if e.code in (400, 403, 404):
                return None
            time.sleep(1.5 * (i + 1))
        except Exception:
            time.sleep(1.5 * (i + 1))
    raise RuntimeError(url)


def day_of(ms):
    return datetime.fromtimestamp(ms / 1000, timezone.utc).date()


def today():
    return datetime.now(timezone.utc).date()


def s3_list(base, prefix, delim='/'):
    out, marker = [], ''
    while True:
        q = f'{base}?prefix={urllib.parse.quote(prefix)}' + (f'&delimiter={delim}' if delim else '') + (f'&marker={urllib.parse.quote(marker)}' if marker else '')
        x = rd(q, raw=True).decode()
        keys = re.findall(r'<Key>([^<]+)</Key>', x) + re.findall(r'<Prefix>([^<]+)</Prefix>', x)[1:]
        out += keys
        if '<IsTruncated>true' not in x:
            return out
        nm = re.findall(r'<NextMarker>([^<]+)</NextMarker>', x)
        marker = nm[0] if nm else re.findall(r'<Key>([^<]+)</Key>', x)[-1]


# ---- Binance
def binance():
    info = rd('https://fapi.binance.com/fapi/v1/exchangeInfo')['symbols']
    return {s['symbol']: s for s in info if s['contractType'] == 'PERPETUAL' and s['quoteAsset'] == 'USDT'}


def binance_all():
    live = binance()
    # листинг S3 (бакет data.binance.vision, виртуальный хост)
    pre = 'data/futures/um/monthly/klines/'
    s3syms = {p[len(pre):].strip('/') for p in s3_list(BN_S3, pre)}
    s3syms = {s for s in s3syms if s.endswith('USDT') and '_' not in s}
    dead = sorted(s3syms - set(live))

    def rest(sym):
        rows, st = [], int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)
        while True:
            k = rd(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&startTime={st}&limit=1500')
            if not k:
                break
            rows += [(day_of(x[0]), float(x[7])) for x in k]
            if len(k) < 1500:
                break
            st = k[-1][0] + 1
        print('binance', sym, len(rows), flush=True)
        return sym, 'rest', rows

    def arch(sym):
        base = f'data/futures/um/monthly/klines/{sym}/1d/'
        rows = []
        for key in s3_list(BN_S3, base):
            if not key.endswith('.zip'):
                continue
            b = rd('https://data.binance.vision/' + key, raw=True)
            if b is None:
                continue
            with zipfile.ZipFile(io.BytesIO(b)) as z, z.open(z.namelist()[0]) as f:
                for line in io.TextIOWrapper(f):
                    p = line.strip().split(',')
                    if p and p[0].isdigit():
                        rows.append((day_of(int(p[0])), float(p[7])))
        print('binance-arch', sym, len(rows), flush=True)
        return sym, 'archive', rows
    with ThreadPoolExecutor(8) as ex:
        a = list(ex.map(rest, sorted(live)))
        b = list(ex.map(arch, dead))
    return a + b, {'live': len(live), 'archive_only': len(dead)}


# ---- Bybit
def bybit_all():
    syms = {}
    for st in ('Trading', 'Closed', 'Delivering'):
        cur = ''
        while True:
            r = rd(f'https://api.bybit.com/v5/market/instruments-info?category=linear&status={st}&limit=1000' + (f'&cursor={urllib.parse.quote(cur)}' if cur else ''))['result']
            for s in r['list']:
                if s['contractType'] == 'LinearPerpetual' and s['quoteCoin'] == 'USDT':
                    syms[s['symbol']] = st
            cur = r.get('nextPageCursor')
            if not cur:
                break
    t0 = int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp() * 1000)

    def one(sym):
        rows, end = [], int(time.time() * 1000)
        while end > t0:
            r = rd(f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=D&end={end}&limit=1000')
            k = (r or {}).get('result', {}).get('list', [])
            if not k:
                break
            rows += [(day_of(int(x[0])), float(x[6])) for x in k]
            if len(k) < 1000:
                break
            end = int(k[-1][0]) - 1
        print('bybit', sym, len(rows), flush=True)
        return sym, syms[sym], [r for r in rows if r[0] >= START]
    with ThreadPoolExecutor(6) as ex:
        return list(ex.map(one, sorted(syms))), {k: sum(1 for v in syms.values() if v == k) for k in ('Trading', 'Closed', 'Delivering')}


# ---- Gate
def gate_all():
    names = set()
    y, m = 2020, 1
    t = today()
    mon = []
    while (y, m) <= (t.year, t.month):
        mon.append('%04d%02d' % (y, m))
        m += 1
        if m == 13:
            y, m = y + 1, 1

    def lst(ym):
        pre = f'futures_usdt/trades/{ym}/'
        return {k[len(pre):].split('-')[0] for k in s3_list('https://gateio-public-data.s3.amazonaws.com/', pre, delim='') if k.endswith('.csv.gz')}
    with ThreadPoolExecutor(6) as ex:
        for s in ex.map(lst, mon):
            names |= s
    print('gate names', len(names), flush=True)
    live = {c['name'] for c in rd('https://api.gateio.ws/api/v4/futures/usdt/contracts') if isinstance(c, dict)}
    names |= live
    names = {n for n in names if n.endswith('_USDT')}

    def one(n):
        rows, fr = [], int(datetime(2020, 1, 1, tzinfo=timezone.utc).timestamp())
        to = int(time.time())
        while fr < to:
            k = rd(f'https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={n}&interval=1d&from={fr}&to={min(to, fr + 1999 * 86400)}')
            if k:
                rows += [(day_of(x['t'] * 1000), float(x['sum'])) for x in k]
            fr += 2000 * 86400
            time.sleep(0.05)
        print('gate', n, len(rows), flush=True)
        return n, 'live' if n in live else 'gone', rows
    with ThreadPoolExecutor(4) as ex:
        return list(ex.map(one, sorted(names))), {'in_archive_listing': len(names), 'live': len(live & names)}


def write(out, ex, data):
    th = TH[ex]
    n_union, ivs, src = {}, [], []
    with gzip.open(os.path.join(out, f'turnover-{ex}.csv.gz'), 'wt', newline='') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'day', 'turnover_usdt', 'med30_prev', 'n30_prev'])
        for sym, source, rows in data:
            by = {}
            for d, v in rows:
                by[d] = v  # повтор дня — последний
            if not by:
                src.append((ex, sym, source, 0, '', ''))
                continue
            days = sorted(by)
            mon = defaultdict(list)
            cur_from = prev = None
            for d in days:
                win = [by[d - timedelta(k)] for k in range(1, 31) if (d - timedelta(k)) in by]
                med = statistics.median(win) if win else None
                w.writerow([ex, sym, d.isoformat(), f'{by[d]:.2f}', '' if med is None else f'{med:.2f}', len(win)])
                mon[(d.year, d.month)].append(by[d])
                ok = med is not None and med >= th
                if ok and cur_from is not None and prev == d - timedelta(1):
                    pass
                elif ok:
                    if cur_from is not None:
                        ivs.append((ex, sym, cur_from, prev))
                    cur_from = d
                else:
                    if cur_from is not None:
                        ivs.append((ex, sym, cur_from, prev))
                    cur_from = None
                prev = d
            if cur_from is not None:
                ivs.append((ex, sym, cur_from, prev))
            best = max(((statistics.median(v), k) for k, v in mon.items()))
            good = sorted(k for k, v in mon.items() if statistics.median(v) >= th)
            n_union[sym] = (len(good), '%04d-%02d' % good[0] if good else '', '%04d-%02d' % good[-1] if good else '', f'{best[0]:.0f}', '%04d-%02d' % best[1])
            src.append((ex, sym, source, len(days), days[0].isoformat(), days[-1].isoformat()))
    with open(os.path.join(out, f'universe-{ex}.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'from_day', 'to_day'])
        for r in ivs:
            w.writerow([r[0], r[1], r[2].isoformat(), r[3].isoformat()])
    with open(os.path.join(out, f'union-{ex}.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'months_pass', 'first_pass_month', 'last_pass_month', 'best_month_median', 'best_month'])
        for sym, v in sorted(n_union.items()):
            w.writerow([ex, sym, *v])
    with open(os.path.join(out, f'sources-{ex}.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'source', 'days_with_candle', 'first_day', 'last_day'])
        w.writerows(src)
    print(ex, 'symbols', len(data), 'union', sum(1 for v in n_union.values() if v[0] > 0), flush=True)


def main():
    out = sys.argv[1]
    only = sys.argv[sys.argv.index('--only') + 1].split(',') if '--only' in sys.argv else list(TH)
    os.makedirs(out, exist_ok=True)
    fn = {'binance-um': binance_all, 'bybit-linear': bybit_all, 'gate-futures_usdt': gate_all}
    for ex in only:
        t = time.time()
        data, meta = fn[ex]()
        print(ex, meta, f'{time.time() - t:.0f} с', flush=True)
        write(out, ex, data)


if __name__ == '__main__':
    main()
