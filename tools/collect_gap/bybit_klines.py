#!/usr/bin/env python3
"""Сверка объёма сделок шарда Bybit по минутам с REST-свечами 1 м: наш объём (сумма `q` записей `t` по минуте времени биржи E = приём_мс − lag) против `volume` свечи.

Нужна для символов, которых REST `recent-trade` не покрывает (1 000 последних сделок короче окна). Минуты окна — `--from`/`--to` (UTC, ЧЧ:ММ, текущие сутки), для каждой — число
символов с расхождением > 1e-9 относительно (свеча больше нашего — потеря, меньше — лишнее). Запуск: python3 bybit_klines.py <корень> <запуск> <шард> --day 2026-11-16 --from 06:38 --to 07:00
"""
import argparse, calendar, collections, glob, json, os, subprocess, time, urllib.request


def ours(root, run, shard, day):
    vol = collections.defaultdict(float)
    for f in sorted(glob.glob(os.path.join(root, day, f'{shard}-{run}.tsv.zst'))):
        out = subprocess.run(['zstd', '-dc', f], capture_output=True).stdout.decode('utf-8', 'replace')
        rx = 0
        for ln in out.split('\n'):
            p = ln.split('\t')
            if len(p) < 3:
                continue
            try:
                rx += int(p[1])
            except ValueError:
                continue
            if p[0] != 't' or len(p) < 8:
                continue
            try:
                lag = int(float(p[2]))
                e_ms = rx // 1_000_000 - lag
                vol[(p[4], e_ms // 60000)] += float(p[7])
            except ValueError:
                continue
    return vol


def kline(sym, t0, t1):
    url = f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=1&start={t0}&end={t1}&limit=100'
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                return json.loads(r.read())['result']['list']
        except Exception:  # noqa
            time.sleep(1)
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('run')
    ap.add_argument('shard')
    ap.add_argument('--day', required=True)
    ap.add_argument('--from', dest='a', required=True)
    ap.add_argument('--to', dest='b', required=True)
    a = ap.parse_args()
    d0 = calendar.timegm(time.strptime(a.day, '%Y-%m-%d'))
    hh = lambda s: int(s[:2]) * 3600 + int(s[3:5]) * 60  # noqa: E731
    t0, t1 = (d0 + hh(a.a)) * 1000, (d0 + hh(a.b)) * 1000
    vol = ours(a.root, a.run, a.shard, a.day)
    syms = sorted({k[0] for k in vol})
    bad = collections.defaultdict(list)
    n_min = collections.Counter()
    for s in syms:
        kl = kline(s, t0, t1)
        time.sleep(0.12)
        if kl is None:
            print('kline error', s)
            continue
        for row in kl:  # [start, open, high, low, close, volume, turnover]
            m = int(row[0]) // 60000
            if not t0 // 60000 <= m <= t1 // 60000:
                continue
            kv, ov = float(row[5]), vol.get((s, m), 0.0)
            n_min[m] += 1
            if abs(kv - ov) > 1e-9 * max(1.0, kv):
                bad[m].append((s, kv, round(ov, 12)))
    print(json.dumps({'symbols': len(syms), 'minutes': {time.strftime('%H:%M', time.gmtime(m * 60)): {'symbols': n_min[m], 'mismatch': len(bad.get(m, [])), 'examples': bad.get(m, [])[:4]}
                                                    for m in sorted(n_min)}}, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
