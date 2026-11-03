#!/usr/bin/env python3
"""TK-488 (В-93): вселенная TradFi-контрактов по дням, порог 1 млн USDT на ВСЕХ трёх биржах (Binance для этого класса — 1 млн,
не 5), и список недостающих свечей 1 м. Оборот не пересчитывается (В-213): берётся из таблиц TK-476 (`turnover-<биржа>.csv.gz`),
у контрактов, чей оборот в таблице кончается раньше конца таблицы (Binance TRADIFI_PERPETUAL: в TK-476 шли как «снятые» через месячный архив — оборот
только до 30.09), и у контрактов без строк оборота дневные свечи доберутся дельтой тем же REST, что в tk476_universe.py (turnover-delta.csv). Правило прохождения и формат выходных файлов — функция `write` из tk476_universe (порог переопределён).
usage: tk488_universe.py <tradfi-list.csv> <каталог universe TK-476> <coverage2.csv TK-476> <out> [--no-fetch] [--last-day YYYY-MM-DD]
--last-day — до какого дня вселенная требует свечи (умолчание = последняя минута лежащих файлов; В-93 возврат: до конца вселенной, 11-02)
Выход в <out>/: turnover-/universe-/union-/sources-<биржа>.csv(.gz) как в TK-476 (только TradFi, порог 1 млн);
turnover-delta.csv (что добрано REST); need-candles.csv (exchange,symbol,class,from_day,to_day,have_first,have_last,need) — что вселенная требует сверх лежащего;
no-turnover.csv (контракты списка без единого дня оборота и причина). Крипто-индексы, крипто pre-market и котировка не USDT — в вселенную не входят."""
import csv, gzip, importlib.util, os, sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('tk476u', os.path.join(HERE, 'tk476_universe.py'))
u = importlib.util.module_from_spec(spec)
spec.loader.exec_module(u)

TH_TRADFI = 1e6
EXCH = ('binance-um', 'bybit-linear', 'gate-futures_usdt')


def tradfi_rows(path):
    """Строки списка, входящие во вселенную: не крипто-индекс, не крипто pre-market (Binance PREDICTUSDT: подтип без TradFi — это крипто
    1–5 млн, В-93 их не трогает), котировка USDT."""
    out = []
    for r in csv.DictReader(open(path, encoding='utf-8')):
        if r['flag'].startswith(('crypto-index', 'pre-market крипто', 'котировка')):
            continue
        out.append(r)
    return out


def load_turnover(unidir, ex, syms):
    """(sym → {day: turnover}) из таблицы TK-476, только нужные контракты."""
    by = defaultdict(dict)
    with gzip.open(os.path.join(unidir, f'turnover-{ex}.csv.gz'), 'rt') as f:
        rd = csv.reader(f)
        next(rd)
        for r in rd:
            if r[1] in syms:
                by[r[1]][date.fromisoformat(r[2])] = float(r[3])
    return by


def fetch_days(ex, sym, since=None):
    """Дневной оборот контракта с дня `since` (None — с 2020) — тот же REST, что в tk476_universe; неполные сутки (сегодня UTC) не берём."""
    t0 = datetime(2020, 1, 1, tzinfo=timezone.utc) if since is None else datetime(since.year, since.month, since.day, tzinfo=timezone.utc)
    t0ms, today = int(t0.timestamp() * 1000), u.today()
    rows = []
    if ex == 'binance-um':
        st = t0ms
        while True:
            k = u.rd(f'https://fapi.binance.com/fapi/v1/klines?symbol={sym}&interval=1d&startTime={st}&limit=1500')
            if not k:
                break
            rows += [(u.day_of(x[0]), float(x[7])) for x in k]
            if len(k) < 1500:
                break
            st = k[-1][0] + 1
    elif ex == 'bybit-linear':
        end = int(datetime.now(timezone.utc).timestamp() * 1000)
        while end > t0ms:
            r = u.rd(f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=D&end={end}&limit=1000')
            k = (r or {}).get('result', {}).get('list', [])
            if not k:
                break
            rows += [(u.day_of(int(x[0])), float(x[6])) for x in k]
            if len(k) < 1000:
                break
            end = int(k[-1][0]) - 1
    return [(d, v) for d, v in rows if t0.date() <= d < today]  # Gate: все контракты списка уже в таблице TK-476, дельта не нужна


def intervals(path):
    out = defaultdict(list)
    for r in csv.DictReader(open(path)):
        out[r['symbol']].append((date.fromisoformat(r['from_day']), date.fromisoformat(r['to_day'])))
    return out


def need_candles(ivs, have, last_day):
    """Требуемое вселенной сверх лежащего файла. ivs — [(from_day, to_day)], have — (первая_utc, последняя_utc) | None.
    Сутки позже last_day (последняя минута архива свечей TK-476) не требуются — их закачка вне этого тикета. Возврат: строка или ''."""
    need = []
    for a, b in ivs:
        b = min(b, last_day)
        if a > b:
            continue
        if have is None:
            need.append(f'нет файла {a}…{b}')
            continue
        hf, hl = have
        if a < hf.date():
            need.append(f'до первой {a}…{min(b, hf.date() - timedelta(1))}')
        if b > hl.date():
            need.append(f'после последней {max(a, hl.date())}…{b}')
    return '; '.join(need)


def main():
    lst, unidir, covf, out = sys.argv[1:5]
    fetch = '--no-fetch' not in sys.argv
    os.makedirs(out, exist_ok=True)
    rows = tradfi_rows(lst)
    cov = {}
    for r in csv.DictReader(open(covf, encoding='utf-8')):
        if r['первая_utc']:
            cov[(r['биржа'], r['символ'])] = (datetime.strptime(r['первая_utc'], '%Y-%m-%d %H:%M'), datetime.strptime(r['последняя_utc'], '%Y-%m-%d %H:%M'))
    last_day = max((v[1] for v in cov.values()), default=datetime.now()).date()
    if '--last-day' in sys.argv:
        last_day = date.fromisoformat(sys.argv[sys.argv.index('--last-day') + 1])
    nt, needs, delta = [], [], {}
    for ex in EXCH:
        mine = [r for r in rows if r['exchange'] == ex]
        cls = {r['symbol']: r['class'] for r in mine}
        by = load_turnover(unidir, ex, set(cls))
        end = max((max(v) for v in by.values()), default=None)
        for sym in sorted(cls):
            if not fetch:
                if sym not in by:
                    nt.append((ex, sym, cls[sym], 'не запрашивали (--no-fetch)'))
                continue
            if sym in by and by[sym] and end is not None and max(by[sym]) >= end:
                continue
            got = fetch_days(ex, sym, max(by[sym]) + timedelta(1) if by.get(sym) else None)  # дельта к таблице TK-476 (В-213)
            if got:
                by.setdefault(sym, {}).update(got)
                delta[(ex, sym)] = (min(d for d, _ in got), max(d for d, _ in got), len(got))
            elif sym not in by:
                nt.append((ex, sym, cls[sym], 'нет дневных свечей на бирже'))
        data = [(s, 'tk476' if s in by else '', sorted(by[s].items())) for s in sorted(by)]
        u.TH[ex] = TH_TRADFI
        u.write(out, ex, data)
        iv = intervals(os.path.join(out, f'universe-{ex}.csv'))
        for sym in sorted(iv):
            needs.append((ex, sym, cls[sym], min(a for a, _ in iv[sym]), max(b for _, b in iv[sym]), *[x.strftime('%Y-%m-%d %H:%M') if x else '' for x in (cov.get((ex, sym)) or (None, None))],
                          need_candles(iv[sym], cov.get((ex, sym)), last_day)))
    with open(os.path.join(out, 'need-candles.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'class', 'from_day', 'to_day', 'have_first', 'have_last', 'need'])
        w.writerows(needs)
    with open(os.path.join(out, 'no-turnover.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'class', 'reason'])
        w.writerows(nt)
    with open(os.path.join(out, 'turnover-delta.csv'), 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'first_day', 'last_day', 'days'])
        w.writerows((*k, *v) for k, v in sorted(delta.items()))
    print('universe contracts', len(needs), 'need', sum(1 for n in needs if n[-1]), 'no-turnover', len(nt))


if __name__ == '__main__':
    main()
