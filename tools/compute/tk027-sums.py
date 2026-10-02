#!/usr/bin/env python3
# TK-027: сверка результатов счёта между машинами — sha256 содержимого каждого файла дерева b5/<вариант>/<сутки>/**.
# Вырезается РОВНО ОДНА строка на файл: начинается с '#' и содержит 'root=' (путь корня входов); ничего больше. Файл, где таких
# строк больше одной, помечается dropped>1 (сверка его не принимает).
#   tk027-sums.py dump <b5> <сутки[,сутки…]>            > sums-<машина>.csv   (путь,sha256,строк,вырезано)
#   tk027-sums.py cmp <a.csv> <b.csv> [сутки[,сутки…]]                       (итог по суткам; код 1 при расхождении)
import hashlib, os, sys

NL = bytes([10])


def dump(b5, days):
    print('path,sha256,lines,dropped')
    for dp, dn, fs in sorted(os.walk(b5)):
        rel = os.path.relpath(dp, b5).split(os.sep)
        if rel[0] != '.' and rel[0].startswith('.'):
            dn[:] = []
            continue
        if len(rel) >= 2 and rel[1] not in days:
            dn[:] = []
            continue
        for f in sorted(fs):
            lines = open(os.path.join(dp, f), 'rb').read().split(NL)
            keep = [l for l in lines if not (l.startswith(b'#') and b'root=' in l)]
            print('/'.join(rel + [f]) + f',{hashlib.sha256(NL.join(keep)).hexdigest()},{len(keep)},{len(lines) - len(keep)}')


def load(p):
    d = {}
    for l in open(p).read().split('\n')[1:]:
        if l:
            k, h, n, dr = l.rsplit(',', 3)
            d[k] = (h, int(dr))
    return d


def cmp(a, b, days):
    A, B = load(a), load(b)
    bad = 0
    for day in days or sorted({k.split('/')[1] for k in list(A) + list(B)}):
        ka = {k for k in A if f'/{day}/' in k}
        kb = {k for k in B if f'/{day}/' in k}
        diff = sorted(k for k in ka & kb if A[k][0] != B[k][0])
        multi = sorted(k for k in ka | kb if (A.get(k) or B.get(k))[1] > 1 or (k in A and k in B and A[k][1] != B[k][1]))
        print(f'{day}: a {len(ka)} b {len(kb)} равны {len(ka & kb) - len(diff)} различны {len(diff)} только_a {len(ka - kb)} только_b {len(kb - ka)} вырезано>1 или разное {len(multi)}')
        for k in diff[:20]:
            print('  DIFF', k)
        bad += len(diff) + len(ka ^ kb) + len(multi)
    sys.exit(1 if bad else 0)


if __name__ == '__main__':
    if sys.argv[1] == 'dump':
        dump(sys.argv[2], set(sys.argv[3].split(',')))
    else:
        cmp(sys.argv[2], sys.argv[3], sys.argv[4].split(',') if len(sys.argv) > 4 else None)
