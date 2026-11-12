#!/usr/bin/env python3
"""TK-388 §1: реплика — episodes.csv нового скрипта на 15-22.10 со старым тарифом = эталон TK-386 (все столбцы, кроме нового dayidx)."""
import csv
REF = {'Q': '/data/tk0380/tk386/ctl/Q-med', 'SUE': '/data/tk0380/tk386/bsc-med', 'LAPTOP': '/data/tk0380/tk386/base-med'}
for c, r in REF.items():
    a = list(csv.DictReader(open(f'{r}/episodes.csv'))); b = list(csv.DictReader(open(f'/data/tk0388/rep/{c}/episodes.csv')))
    for x in b: x.pop('dayidx', None)
    print(c, 'эталон', len(a), 'новый', len(b), 'БАЙТ-В-БАЙТ' if a == b else 'РАСХОЖДЕНИЕ')
