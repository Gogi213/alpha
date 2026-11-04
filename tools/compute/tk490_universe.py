#!/usr/bin/env python3
"""TK-490 (В-94, G-017): вселенная пампов — USDT-перпетуалы Bybit и Binance USD-M, порог 1 млн USDT оборота на обеих биржах
(Binance 1 млн, не 5: В-93), по дням, период 2025-01-01 … LAST. Правило и код оборота — tk476_universe.py (медиана суточного
оборота за 30 предыдущих суток >= порога, снятые — до даты снятия); окно медианы берётся с 2024-12-01, чтобы 2025-01-01 имел
полные 30 суток. Класс — список TK-488 (tk488_tradfi_list.py): TradFi-контракт в закачку не идёт, остаётся строкой с классом.
Снятые контракты в живых метаданных биржи отсутствуют (Binance — только SETTLING): их класс неизвестен, они считаются
крипто и перечислены в unclassified-<биржа>.csv (В-172: не дорисовываем).
usage: tk490_universe.py <out> [--tradfi-list <csv>] [--last-day YYYY-MM-DD] [--only binance-um,bybit-linear]
Выход в <out>/: turnover-/universe-/union-/sources-<биржа>.csv(.gz) как в TK-476 (без обрезки), pump-universe.csv
(exchange,symbol,class,from_day,to_day — интервалы прохождения порога, обрезанные до периода), pump-universe-daily.csv
(day,binance-um,bybit-linear — контрактов во вселенной в этот день), symbols-<биржа>.txt (контракты крипто-класса с хотя бы
одним днём во вселенной — список закачки), tradfi-skipped.csv, unclassified-<биржа>.csv."""
import csv, importlib.util, os, subprocess, sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, name + '.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


PERIOD_FROM = date(2025, 1, 1)
WINDOW_FROM = date(2024, 12, 1)  # 31 сутки до периода: медиана 30 предыдущих суток для 2025-01-01
EXCH = ('binance-um', 'bybit-linear')
TH = 1e6


def clip(ivs, lo, hi):
    """Интервалы (symbol, from, to) включительно, обрезанные до [lo, hi]; пустые отбрасываются."""
    out = []
    for sym, a, b in ivs:
        a, b = max(a, lo), min(b, hi)
        if a <= b:
            out.append((sym, a, b))
    return out


def daily_counts(ivs_by_ex, lo, hi):
    """day -> {биржа: контрактов во вселенной}."""
    cnt = {}
    d = lo
    while d <= hi:
        cnt[d] = {ex: 0 for ex in ivs_by_ex}
        d += timedelta(1)
    for ex, ivs in ivs_by_ex.items():
        for _, a, b in ivs:
            d = max(a, lo)
            while d <= min(b, hi):
                cnt[d][ex] += 1
                d += timedelta(1)
    return cnt


def main():
    out = sys.argv[1]
    arg = lambda k, dflt=None: sys.argv[sys.argv.index(k) + 1] if k in sys.argv else dflt
    last = date.fromisoformat(arg('--last-day', (datetime.now(timezone.utc).date() - timedelta(1)).isoformat()))
    only = arg('--only', ','.join(EXCH)).split(',')
    os.makedirs(out, exist_ok=True)
    lst = arg('--tradfi-list', os.path.join(out, 'tradfi-list.csv'))
    if not os.path.exists(lst):
        subprocess.run([sys.executable, os.path.join(HERE, 'tk488_tradfi_list.py'), lst], check=True)
    u = load('tk476_universe')
    t88 = load('tk488_universe')
    u.START = WINDOW_FROM  # Bybit: обрезка строк при чтении; Binance — обрезка ниже
    for ex in EXCH:
        u.TH[ex] = TH
    tradfi = {(r['exchange'], r['symbol']): r['class'] for r in t88.tradfi_rows(lst)}
    fn = {'binance-um': u.binance_all, 'bybit-linear': u.bybit_all}
    ivs_by_ex, rows_out, skipped, live_names = {}, [], [], {}
    for ex in only:
        data, meta = fn[ex]()
        print(ex, meta, flush=True)
        data = [(s, src, [r for r in rows if WINDOW_FROM <= r[0] <= last]) for s, src, rows in data]
        u.write(out, ex, data)
        live_names[ex] = {s for s, src, _ in data if src in ('rest', 'Trading')}
        ivs = defaultdict(list)
        for r in csv.DictReader(open(os.path.join(out, f'universe-{ex}.csv'))):
            ivs[r['symbol']].append((r['symbol'], date.fromisoformat(r['from_day']), date.fromisoformat(r['to_day'])))
        flat = clip([x for v in ivs.values() for x in v], PERIOD_FROM, last)
        crypto = []
        for sym, a, b in sorted(flat, key=lambda x: (x[0], x[1])):
            cls = tradfi.get((ex, sym))
            rows_out.append([ex, sym, cls or 'crypto', a.isoformat(), b.isoformat()])
            (skipped if cls else crypto).append((sym, a, b))
        ivs_by_ex[ex] = crypto
        with open(os.path.join(out, f'symbols-{ex}.txt'), 'w') as f:
            f.write(''.join(s + '\n' for s in sorted({x[0] for x in crypto})))
        with open(os.path.join(out, f'unclassified-{ex}.csv'), 'w', newline='') as f:
            w = csv.writer(f)
            w.writerow(['exchange', 'symbol', 'note'])
            for s in sorted({x[0] for x in crypto} - live_names[ex]):
                w.writerow([ex, s, 'снят: класса в метаданных биржи нет, принят крипто'])
        n_tf = len({r[1] for r in rows_out if r[0] == ex and r[2] != 'crypto'})
        print(ex, 'контрактов крипто', len({x[0] for x in crypto}), 'TradFi пропущено', n_tf, flush=True)
    with open(os.path.join(out, 'pump-universe.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'class', 'from_day', 'to_day'])
        w.writerows(rows_out)
    with open(os.path.join(out, 'tradfi-skipped.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['exchange', 'symbol', 'class'])
        w.writerows(sorted({(r[0], r[1], r[2]) for r in rows_out if r[2] != 'crypto'}))
    cnt = daily_counts(ivs_by_ex, PERIOD_FROM, last)
    with open(os.path.join(out, 'pump-universe-daily.csv'), 'w', newline='') as f:
        w = csv.writer(f)
        w.writerow(['day', *ivs_by_ex])
        for d in sorted(cnt):
            w.writerow([d.isoformat(), *[cnt[d][ex] for ex in ivs_by_ex]])
    open(os.path.join(out, 'universe.done'), 'w').write('ok\n')
    print('done', flush=True)


if __name__ == '__main__':
    main()
