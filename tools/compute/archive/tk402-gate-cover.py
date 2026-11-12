#!/usr/bin/env python3
"""TK-402 охват Gate: по монете — часов из 173 (15.10 00 … 22.10 04), причина каждой дыры, вырожденные часы (сырых строк < 100).
Запуск: tk402-gate-cover.py GEVDIR OUT.tsv; код 1, если у монеты нет ни одного часа (неучтённая дыра)."""
import sys, os, datetime as dt
d, out = sys.argv[1], sys.argv[2]
H = []; t = dt.datetime(2026, 10, 15)
while t <= dt.datetime(2026, 10, 22, 4):
    H.append(t.strftime('%Y%m%d%H')); t += dt.timedelta(hours=1)
bad = 0
with open(out, 'w', encoding='utf-8', newline='\n') as o:
    o.write('coin\thours_ok\tmissing_before_first\tmissing_after_first\tdegenerate\treason\n')
    for c in sorted(os.listdir(d)):
        have = {h for h in H if os.path.exists(f'{d}/{c}/{h}.npy')}
        if not have:
            o.write(f'{c}\t0\t{len(H)}\t0\t0\tнет ни одного часа\n'); bad += 1; continue
        f = min(have)
        pre = [h for h in H if h < f]; post = [h for h in H if h > f and h not in have]
        deg = []
        for h in have:
            try: n = int(open(f'{d}/{c}/{h}.n').read())
            except OSError: n = -1
            if n < 100: deg.append(h)
        r = []
        if pre: r.append(f'листинг/начало архива {f} ({len(pre)} ч до)')
        if post: r.append(f'дыра в архиве после {f}: {len(post)} ч')
        if deg: r.append(f'вырожденных часов {len(deg)}')
        o.write(f'{c}\t{len(have)}\t{len(pre)}\t{len(post)}\t{len(deg)}\t{"; ".join(r)}\n')
print('cover written', out, 'без часов:', bad)
sys.exit(1 if bad else 0)
