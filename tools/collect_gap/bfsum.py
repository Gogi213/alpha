#!/usr/bin/env python3
"""Сводка файлов дозапроса `backfill-<биржа>-<запуск>.tsv.zst` (формат — docs/collector-ws-format.md): пропуски `rg` по статусам, номера, `rt`-сделки.

Читает кадры zstd подряд (`zstd -dc`); строки `rh` / `rt` / `rg`. Печатает JSON: по статусам число пропусков, получено номеров, нет на бирже, потеряно;
первые пропуски с символом и шардом. Запуск: python3 bfsum.py <корень данных биржи> <подстрока запуска, напр. 1794812345> [--limit 10]
"""
import argparse, collections, glob, json, os, subprocess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('run')
    ap.add_argument('--limit', type=int, default=10)
    a = ap.parse_args()
    files = sorted(f for f in glob.glob(os.path.join(a.root, '2026-*', 'backfill-*.tsv.zst')) if a.run in os.path.basename(f))
    st = collections.Counter()
    ids = collections.Counter()
    rt = 0
    heads, first = [], []
    for f in files:
        out = subprocess.run(['zstd', '-dc', f], capture_output=True).stdout.decode('utf-8', 'replace')
        for ln in out.split('\n'):
            p = ln.split('\t')
            if not p or not p[0]:
                continue
            if p[0] == 'rh':
                heads.append(ln[:160])
            elif p[0] == 'rt':
                rt += 1
            elif p[0] == 'rg' and len(p) >= 11:
                # rg <мс> <символ> <от> <до> <найден> <получено> <статус> <попыток> <шард> <нет на бирже> <время пропуска>
                try:
                    lo, hi, got, status, absent = int(p[3]), int(p[4]), int(p[6]), p[7], int(p[10])
                except ValueError:
                    continue
                n = hi - lo + 1
                st[status] += 1
                ids['gap_ids'] += n
                ids['got'] += got
                ids['absent'] += absent
                if status not in ('ok',) and len(first) < a.limit:
                    first.append({'sym': p[2], 'from': lo, 'to': hi, 'got': got, 'status': status, 'tries': p[8], 'shard': p[9], 'absent': absent})
    print(json.dumps({'files': [os.path.basename(f) for f in files], 'gaps_by_status': dict(st), 'ids': dict(ids), 'rt_rows': rt, 'heads': heads[:3], 'non_ok': first}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
