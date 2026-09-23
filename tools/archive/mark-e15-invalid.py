#!/usr/bin/env python3
"""Пометить строки невалидного прогона E15 (F10, 21.09 17:37Z) в журнале счётной машины:
kind confirmatory -> amendment (в испытания/DSR не идёт) и префикс 'INVALID (E15 Ф1/Ф2 …)'
в detail. Аудит этапа F §8, план исправления п.1(б)."""
import csv
import sys

p = sys.argv[1] if len(sys.argv) > 1 else 'study/runs-2026-09-19.csv'
rows = list(csv.reader(open(p, encoding='utf-8')))
head, body = rows[0], rows[1:]
n = 0
for r in body:
    if (len(r) >= 4 and r[2] == 'confirmatory' and r[3].startswith('bounce_form')
            and r[0].startswith('2026-09-21T17:')):
        r[2] = 'amendment'
        r[3] = ('INVALID (E15 Ф1/Ф2, не считать в DSR — набор age+flow вместе, '
                'имя ночного a45-bid занято): ') + r[3]
        n += 1
with open(p, 'w', newline='', encoding='utf-8') as f:
    w = csv.writer(f)
    w.writerow(head)
    w.writerows(body)
print('помечено невалидными:', n)
