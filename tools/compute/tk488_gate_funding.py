#!/usr/bin/env python3
"""TK-488 (возврат Судьи Н3): история ставок фандинга Gate (USDT-фьючерсы) для контрактов вселенной TradFi — формат как у TK-478:
<OUT>/gate-futures_usdt/<SYM>.csv.gz = time_ms,rate,interval_h (interval_h — разрыв до предыдущей записи, как у Bybit в TK-478).
REST GET /futures/usdt/funding_rate?contract=&to=&limit=1000 — страницы от конца назад до пустой (t в секундах, rate строкой как отдала биржа;
биржа отдаёт ≤ ~30 суток за запрос и с `from` старше 180 суток отказывает — поэтому без `from`).
Готовый файл не переписывает. Снятые, которых нет в API (ответ 400/404), — в <OUT>/gate-futures_usdt-absent.txt (не выдумываем).
usage: tk488_gate_funding.py <OUT> <каталог universe TK-488> [потоков=4]"""
import csv, importlib.util, os, sys, time
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location('tk488f', os.path.join(HERE, 'tk488_fill.py'))
f = importlib.util.module_from_spec(spec)
spec.loader.exec_module(f)
spec = importlib.util.spec_from_file_location('tk478', os.path.join(HERE, 'tk478-funding.py'))
fund = importlib.util.module_from_spec(spec)
spec.loader.exec_module(fund)

EX = 'gate-futures_usdt'
START_S = 1577836800  # 2020-01-01
GAP = 0.15


def pages(sym, end_s):
    """Все записи (t_с, rate) от end_s назад до начала; None — контракта нет в API."""
    rows, to = [], end_s
    while to >= START_S:
        k = f.get_json(f'https://api.gateio.ws/api/v4/futures/usdt/funding_rate?contract={sym}&to={to}&limit=1000', GAP)
        if k is None:
            return None if not rows else rows
        if not k:
            break
        rows += [(int(x['t']), x['r']) for x in k]
        old = min(int(x['t']) for x in k)
        if old - 1 >= to:
            break
        to = old - 1
    return rows


def one(sym, out, end_s):
    dst = os.path.join(out, EX, sym + '.csv.gz')
    if os.path.exists(dst):
        return sym, 'есть', 0
    r = pages(sym, end_s)
    if r is None:
        return sym, 'нет в API', 0
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    n = fund.write_rows(dst, [(t * 1000, rate, None) for t, rate in r])
    return sym, 'ок', n


def main():
    out, udir = sys.argv[1], sys.argv[2]
    threads = int(sys.argv[3]) if len(sys.argv) > 3 else 4
    os.makedirs(os.path.join(out, EX), exist_ok=True)
    syms = fund.symbols(udir, EX)
    end_s = int(time.time())
    absent = []
    with ThreadPoolExecutor(threads) as ex:
        for s, st, n in ex.map(lambda s: one(s, out, end_s), syms):
            print(s, st, n, flush=True)
            if st == 'нет в API':
                absent.append(s)
    with open(os.path.join(out, EX + '-absent.txt'), 'w') as g:
        g.write(''.join(s + '\n' for s in absent))
    print('символов', len(syms), 'нет в API', len(absent))


if __name__ == '__main__':
    main()
