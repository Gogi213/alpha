#!/usr/bin/env python3
"""TK-488 (возврат Судьи Н5, п.4 договора): проверка вселенной на контрактах с известным ответом. Ответ считается НЕЗАВИСИМО от tk476_universe.write
прямо из дневных оборотов (колонка turnover_usdt таблицы turnover-<биржа>.csv.gz): для каждого дня D — медиана оборота за предыдущие до 30
календарных суток (сортировка и середина вручную), день входит, если медиана >= порога (1 млн), подряд идущие дни -> интервал. Сравнение с
universe-<биржа>.csv; для каждой границы интервала в выход пишутся день, медиана и число суток в окне — глазами проверяется на сырых числах.
usage: tk488_known3.py <каталог universe TK-488> <выход.csv> <биржа:СИМВОЛ> [...]"""
import csv, gzip, os, sys
from datetime import date, timedelta

TH = 1e6


def median(v):
    s = sorted(v)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def expected(by):
    """by: {день: оборот} -> ([(с, по)], {день: (медиана, n)}) по правилу: медиана предыдущих <=30 суток >= TH."""
    ivs, cur, prev, med = [], None, None, {}
    for d in sorted(by):
        win = [by[d - timedelta(k)] for k in range(1, 31) if (d - timedelta(k)) in by]
        m = median(win) if win else None
        med[d] = (m, len(win))
        ok = m is not None and m >= TH
        if ok and cur is not None and prev == d - timedelta(1):
            pass
        else:
            if cur is not None:
                ivs.append((cur, prev))
            cur = d if ok else None
        prev = d
    if cur is not None:
        ivs.append((cur, prev))
    return ivs, med


def turnover(unidir, ex, sym):
    by = {}
    with gzip.open(os.path.join(unidir, f'turnover-{ex}.csv.gz'), 'rt') as f:
        for r in csv.DictReader(f):
            if r['symbol'] == sym:
                by[date.fromisoformat(r['day'])] = float(r['turnover_usdt'])
    return by


def got(unidir, ex, sym):
    return [(date.fromisoformat(r['from_day']), date.fromisoformat(r['to_day'])) for r in csv.DictReader(open(os.path.join(unidir, f'universe-{ex}.csv'))) if r['symbol'] == sym]


def main():
    unidir, out = sys.argv[1], sys.argv[2]
    with open(out, 'w', newline='', encoding='utf-8') as f:
        w = csv.writer(f)
        w.writerow(['биржа', 'символ', 'что', 'день', 'медиана_30_пред', 'суток_в_окне', 'оборот_дня', 'ожидаемо', 'во_вселенной', 'совпало'])
        for arg in sys.argv[3:]:
            ex, sym = arg.split(':')
            by = turnover(unidir, ex, sym)
            ivs, med = expected(by)
            g = got(unidir, ex, sym)
            for a, b in ivs:
                for what, d in (('вход', a), ('последний_день', b), ('день_до_входа', a - timedelta(1)), ('день_после_выхода', b + timedelta(1))):
                    if d not in med:
                        continue
                    m, n = med[d]
                    inside = any(x <= d <= y for x, y in g)
                    want = what in ('вход', 'последний_день')
                    w.writerow([ex, sym, what, d, '' if m is None else f'{m:.2f}', n, f'{by[d]:.2f}', 'в' if want else 'вне', 'в' if inside else 'вне', int(inside == want)])
            w.writerow([ex, sym, 'интервалы', '', '', '', '', ';'.join(f'{a}..{b}' for a, b in ivs) or '-', ';'.join(f'{a}..{b}' for a, b in g) or '-', int(ivs == g)])
            print(ex, sym, 'ожидаемо', ivs, 'во вселенной', g, 'СОВПАЛО' if ivs == g else 'РАСХОЖДЕНИЕ')


if __name__ == '__main__':
    main()
