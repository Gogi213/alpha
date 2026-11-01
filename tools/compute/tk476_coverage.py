#!/usr/bin/env python3
"""TK-476: покрытие минутных свечей по файлам: первая/последняя минута, строк, ожидаемо, пропущено, дыры, дубли.
Читает файл целиком (битый gzip = ошибка символа в колонке `err`, прогон не падает). Минут не выдумывает.
usage: tk476_coverage.py <выход.csv> <JOBS> <биржа=каталог> [<биржа=каталог> ...]"""
import csv, datetime as dt, os, sys
from multiprocessing import Pool

import numpy as np
import pandas as pd

COLS = ['биржа', 'символ', 'первая_utc', 'последняя_utc', 'строк', 'ожидаемо_минут', 'пропущено_минут', 'дыр', 'макс_дыра_мин',
        'дублей', 'не_по_порядку', 'не_на_минуте', 'ошибка']


def utc(ms):
    return dt.datetime.fromtimestamp(ms / 1000, dt.timezone.utc).strftime('%Y-%m-%d %H:%M')


def one(a):
    ex, path = a
    sym = os.path.basename(path)[:-7]
    try:
        t = pd.read_csv(path, usecols=[0], dtype='int64').iloc[:, 0].to_numpy()
    except Exception as e:  # noqa: BLE001 — любая порча файла = строка в таблице
        return [ex, sym, '', '', 0, 0, 0, 0, 0, 0, 0, 0, type(e).__name__ + ': ' + str(e)[:80]]
    if len(t) == 0:
        return [ex, sym, '', '', 0, 0, 0, 0, 0, 0, 0, 0, 'пусто']
    d = np.diff(t)
    gap = d[d > 60000] // 60000 - 1
    exp = (int(t.max()) - int(t.min())) // 60000 + 1
    return [ex, sym, utc(int(t.min())), utc(int(t.max())), len(t), exp, int(gap.sum()), len(gap), int(gap.max()) if len(gap) else 0,
            int((d == 0).sum()), int((d < 0).sum()), int((t % 60000 != 0).sum()), '']


def main():
    out, jobs = sys.argv[1], int(sys.argv[2])
    items = []
    for spec in sys.argv[3:]:
        ex, d = spec.split('=', 1)
        items += [(ex, f'{d}/{f}') for f in sorted(os.listdir(d)) if f.endswith('.csv.gz')]
    with Pool(jobs) as p, open(out, 'w', newline='', encoding='utf-8') as o:
        w = csv.writer(o); w.writerow(COLS)
        for i, r in enumerate(p.imap(one, items, chunksize=4), 1):
            w.writerow(r)
            if i % 200 == 0: print(i, len(items), flush=True)
    print('готово', len(items), flush=True)


if __name__ == '__main__':
    main()
