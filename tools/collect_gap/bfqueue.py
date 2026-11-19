#!/usr/bin/env python3
"""Очередь дозапроса BingX по файлу `backfill-bingx-<запуск>.tsv.zst`: порядок запросов против срока окна REST и подтверждение пропусков.

По каждому пропуску (`rg`): срок = найден + (окно − номеров) × длительность / номеров (как `GapReq::deadline_ms`, окно 1 000); первое получение сделок (`rt`) − найден =
подтверждение + ожидание в очереди (минимум по пропускам ≈ подтверждение, 2 с у сборки с `BINGX_CONFIRM_MS`). Порядок: пропуски по времени итога `rg`; нарушение —
пропуск взят раньше другого, который к тому моменту уже подтверждён и имел срок раньше (допуск 1,5 с — темп запросов).
Запуск: bfqueue.py <файл .tsv.zst> [--confirm-ms 2000] [--window 1000]"""
import argparse, subprocess


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('file')
    ap.add_argument('--confirm-ms', type=int, default=2000)
    ap.add_argument('--window', type=int, default=1000)
    a = ap.parse_args()
    lines = subprocess.run(['zstd', '-dc', a.file], capture_output=True).stdout.decode(errors='replace').splitlines()
    rg, first_rt = [], {}
    for ln in lines:
        p = ln.split('\t') if '\t' in ln else ln.split(' ')
        if p[0] == 'rg' and len(p) >= 12:
            rg.append(dict(ms=int(p[1]), sym=p[2], lo=int(p[3]), hi=int(p[4]), found=int(p[5]), got=int(p[6]), st=p[7], gap=int(p[11])))
        elif p[0] == 'rt' and len(p) >= 12:
            first_rt.setdefault((p[2], int(p[10]), int(p[11])), int(p[1]))
    if not rg:
        print('no rg rows')
        return
    for r in rg:
        n = r['hi'] - r['lo'] + 1
        r['n'] = n
        r['deadline'] = r['found'] + max(a.window - n, 0) * max(r['found'] - r['gap'], 1) // n
        r['t0'] = first_rt.get((r['sym'], r['lo'], r['hi']), r['ms'])
    rg.sort(key=lambda r: r['t0'])
    viol = 0
    for i, r in enumerate(rg):
        for j in range(i + 1, len(rg)):
            o = rg[j]
            if o['found'] + a.confirm_ms <= r['t0'] - 1500 and o['deadline'] + 1500 < r['deadline']:
                viol += 1
                break
    delays = sorted(r['t0'] - r['found'] for r in rg)
    print('gaps', len(rg), 'ids', sum(r['n'] for r in rg), 'lost', sum(r['n'] - r['got'] for r in rg if r['st'] == 'partial'),
          '| first fetch - found, ms: min %d p50 %d max %d' % (delays[0], delays[len(delays) // 2], delays[-1]),
          '| order violations (taken before a ready gap with an earlier deadline)', viol)


main()
