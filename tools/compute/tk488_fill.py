#!/usr/bin/env python3
"""TK-488 (В-93, возврат Судьи Н1+Н2): докачка минутных свечей REST в стадию для слияния tk476_merge.py — дельта к лежащему (В-213).
Окна на контракт вселенной: (а) хвост — от последней минуты лежащего файла до конца последнего дня вселенной (Binance/Bybit: с
перекрытием в одну минуту — merge сверяет её со старым файлом; Gate: строго после, n_trades REST не отдаёт); (б) дыры — только суточные
окна из --gap-days (Binance: суточный архив 28.10 отдаёт 404, REST минуты отдаёт), если в файле за эти сутки ни одной строки.
Минуты без сделок Gate (v=0) в файл не пишутся (так устроены лежащие, из архива сделок); строки REST пишутся как отдала биржа.
usage: tk488_fill.py <биржа> <каталог universe TK-488> <корень свечей> <стадия> <последний_день YYYY-MM-DD> [--gap-days D1,D2] [--only SYM,SYM]
Выход: <стадия>/<биржа>/<SYM>.csv.gz (t_open_ms,o,h,l,c,v,quote_v,n_trades) и <стадия>/fill-<биржа>.csv (символ, окна, строк, примечание).
Затем: TK476_ROOT=<корень> TK476_STAGE=<стадия> tk476_merge.py <биржа> --apply (сверка перекрытия, прежний файл — в <стадия>/prev)."""
import csv, gzip, importlib.util, json, os, sys, threading, time, urllib.error, urllib.parse, urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
MIN = 60000
UA = {'User-Agent': 'alpha-tk488'}
HDR = 't_open_ms,o,h,l,c,v,quote_v,n_trades\n'
_lk, _next = threading.Lock(), [0.0]


def throttle(gap):
    """Общий темп запросов на процесс: не чаще одного раза в gap секунд."""
    with _lk:
        now = time.monotonic()
        at = max(_next[0], now)
        _next[0] = at + gap
    if at > now:
        time.sleep(at - now)


def get_json(url, gap, tries=8):
    err = None
    for i in range(tries):
        throttle(gap)
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=40) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            err = e
            if e.code == 400 or e.code == 404:
                return None
            time.sleep(2 * (2 ** min(i, 5)) * (6 if e.code in (418, 429, 403) else 1))
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2 * (2 ** min(i, 4)))
    raise RuntimeError(f'{url}: {err}')


def existing_times(path):
    """(множество t_open_ms, первая, последняя) лежащего файла."""
    ts = []
    with gzip.open(path, 'rt') as f:
        next(f)
        for l in f:
            ts.append(int(l.split(',', 1)[0]))
    return ts


def windows(ts, last_ms, gap_days, overlap):
    """Окна докачки [(a_ms, b_ms)] включительно по минутам открытия. ts — минуты лежащего (пусто → окон нет)."""
    if not ts:
        return []
    have_days = {t // 86400000 for t in ts}
    out = []
    for d in gap_days:
        d0 = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000)
        if d0 // 86400000 in have_days or ts[0] > d0 + 86400000 - MIN or ts[-1] < d0:
            continue  # в файле за сутки есть строки / контракт ещё не торговался / уже снят
        out.append((d0, d0 + 86400000 - MIN))
    hi = max(ts)
    if hi < last_ms:
        out.append((hi if overlap else hi + MIN, last_ms))
    return out


def fetch_binance(sym, a, b, gap):
    rows, st = [], a
    while st <= b:
        k = get_json(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1m&startTime={st}&endTime={b}&limit=1500', gap)
        if not k:
            break
        rows += [f'{x[0]},{x[1]},{x[2]},{x[3]},{x[4]},{x[5]},{x[7]},{x[8]}\n' for x in k]
        if len(k) < 1500:
            break
        st = k[-1][0] + MIN
    return rows


def fetch_bybit(sym, a, b, gap):
    rows, st = [], a
    while st <= b:
        en = min(st + 999 * MIN, b)
        d = get_json(f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=1&start={st}&end={en}&limit=1000', gap)
        lst = sorted(((d or {}).get('result') or {}).get('list') or [], key=lambda k: int(k[0]))
        rows += [f'{k[0]},{k[1]},{k[2]},{k[3]},{k[4]},{k[5]},{k[6]},\n' for k in lst if a <= int(k[0]) <= b]
        st = en + MIN
    return rows


def quanto(sym, gap):
    c = get_json(f'https://api.gateio.ws/api/v4/futures/usdt/contracts/{sym}', gap)
    return float(c['quanto_multiplier']) if c else None


def fetch_gate(sym, a, b, gap):
    q = quanto(sym, gap)
    if q is None:
        return None  # контракта нет в живом API (снят)
    rows, st = [], a
    while st <= b:
        en = min(st + 1999 * MIN, b)
        k = get_json(f'https://api.gateio.ws/api/v4/futures/usdt/candlesticks?contract={sym}&interval=1m&from={st // 1000}&to={en // 1000}', gap) or []
        for x in sorted(k, key=lambda x: x['t']):
            if x['v'] == 0 or not a <= x['t'] * 1000 <= b:
                continue  # минута без сделок: в лежащих (из архива сделок) таких строк нет
            rows.append(f"{x['t'] * 1000},{x['o']},{x['h']},{x['l']},{x['c']},{abs(x['v']) * q:.12g},{x['sum']},\n")
        st = en + MIN
    return rows


FETCH = {'binance-um': (fetch_binance, 0.25, True), 'bybit-linear': (fetch_bybit, 0.06, True), 'gate-futures_usdt': (fetch_gate, 0.12, False)}


def one(ex, sym, last_ms, gap_days, root, stage):
    fetch, gap, overlap = FETCH[ex]
    path = f'{root}/{ex}/{sym}.csv.gz'
    if not os.path.exists(path):
        return [sym, '', 0, 'нет лежащего файла (не дельта)']
    ts = existing_times(path)
    wins = windows(ts, last_ms, gap_days, overlap)
    if not wins:
        return [sym, '', 0, 'докачивать нечего']
    rows = []
    for a, b in wins:
        r = fetch(sym, a, b, gap)
        if r is None:
            return [sym, ';'.join(f'{a}-{b}' for a, b in wins), 0, 'контракта нет в живом API биржи']
        rows += r
    uniq = {}
    for l in rows:
        uniq.setdefault(int(l.split(',', 1)[0]), l)
    if not uniq:
        return [sym, ';'.join(f'{a}-{b}' for a, b in wins), 0, 'биржа не отдала свечей в окнах']
    os.makedirs(f'{stage}/{ex}', exist_ok=True)
    tmp = f'{stage}/{ex}/{sym}.csv.gz.part'
    with gzip.open(tmp, 'wt', encoding='ascii', compresslevel=6, newline='') as g:
        g.write(HDR)
        for t in sorted(uniq):
            g.write(uniq[t])
    os.replace(tmp, f'{stage}/{ex}/{sym}.csv.gz')
    return [sym, ';'.join(f'{a}-{b}' for a, b in wins), len(uniq), '']


def universe_symbols(udir, ex):
    """Контракты вселенной, чей последний интервал доходит до своего to_day: (символ → последний день)."""
    last = {}
    for r in csv.DictReader(open(f'{udir}/universe-{ex}.csv', encoding='utf-8')):
        d = date.fromisoformat(r['to_day'])
        last[r['symbol']] = max(d, last.get(r['symbol'], d))
    return last


def main():
    ex, udir, root, stage, end_day = sys.argv[1:6]
    gap_days = [date.fromisoformat(x) for x in sys.argv[sys.argv.index('--gap-days') + 1].split(',')] if '--gap-days' in sys.argv else []
    only = set(sys.argv[sys.argv.index('--only') + 1].split(',')) if '--only' in sys.argv else None
    end = date.fromisoformat(end_day)
    last = universe_symbols(udir, ex)
    syms = sorted(s for s in last if not only or s in only)
    os.makedirs(stage, exist_ok=True)

    def job(sym):
        d = min(last[sym], end)
        last_ms = int(datetime(d.year, d.month, d.day, tzinfo=timezone.utc).timestamp() * 1000) + 86400000 - MIN
        return one(ex, sym, last_ms, gap_days, root, stage)

    with ThreadPoolExecutor(int(os.environ.get('THREADS', 6))) as pool, open(f'{stage}/fill-{ex}.csv', 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['символ', 'окна_мс', 'строк_REST', 'примечание'])
        for r in pool.map(job, syms):
            w.writerow(r)
            print(*r, flush=True)


if __name__ == '__main__':
    main()
