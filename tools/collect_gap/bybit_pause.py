#!/usr/bin/env python3
"""Проверка паузы шарда сделок Bybit по REST: была ли настоящая тишина на бирже или потеря у нас.

Вход: каталог данных, запуск (подстрока имени файла), шард, интервалы пауз `gap_start_ms:gap_end_ms` (события `gap` / `gap_end` в `events-<шард>.jsonl`).
Символы шарда — из его файла запуска (строки `t`). REST `GET /v5/market/recent-trade?category=linear&symbol=&limit=1000` — последние 1000 сделок символа: для символа, у которого
самая старая сделка ответа раньше начала окна, окно покрыто. Номер сделки Bybit — UUID (`execId`), в файле — последнее поле строки `t`: сделка REST, UUID которой нет ни в одном файле `trade*`
запуска, — потеря; сделка REST внутри паузы с UUID в файле — биржа доставила её позже (задержка, не потеря); сделок REST внутри паузы нет — тишина на бирже.
Запуск: python3 bybit_pause.py <корень> <запуск> <шард> <от_мс:до_мс> [...] [--margin-s 300]
"""
import argparse, collections, glob, json, os, subprocess, time, urllib.request


def uuids_and_syms(root, run, shard):
    ids, shard_syms = set(), set()
    for f in sorted(glob.glob(os.path.join(root, '2026-*', f'trade*-{run}.tsv.zst'))):
        is_shard = os.path.basename(f).startswith(shard + '-')
        out = subprocess.run(['zstd', '-dc', f], capture_output=True).stdout.decode('utf-8', 'replace')
        for ln in out.split('\n'):
            if not ln.startswith('t\t'):
                continue
            p = ln.split('\t')
            if len(p) < 13:
                continue
            ids.add(p[-1])
            if is_shard:
                shard_syms.add(p[4])
    return ids, shard_syms


def rest_trades(sym):
    url = f'https://api.bybit.com/v5/market/recent-trade?category=linear&symbol={sym}&limit=1000'
    for _ in range(3):
        try:
            with urllib.request.urlopen(url, timeout=15) as r:
                d = json.loads(r.read())
            return d['result']['list']
        except Exception:  # noqa
            time.sleep(1)
    return None


def klines_1m(sym, t0_ms, t1_ms):
    url = f'https://api.bybit.com/v5/market/kline?category=linear&symbol={sym}&interval=1&start={t0_ms}&end={t1_ms}&limit=10'
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())['result']['list']
    except Exception:  # noqa
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('root')
    ap.add_argument('run')
    ap.add_argument('shard')
    ap.add_argument('gaps', nargs='+')
    ap.add_argument('--margin-s', type=int, default=300)
    a = ap.parse_args()
    gaps = [tuple(int(x) for x in g.split(':')) for g in a.gaps]
    ids, syms = uuids_and_syms(a.root, a.run, a.shard)
    lo = min(g[0] for g in gaps) - a.margin_s * 1000
    hi = max(g[1] for g in gaps) + a.margin_s * 1000
    res = {'shard': a.shard, 'symbols': len(syms), 'uuids_in_files': len(ids), 'gaps': [list(g) for g in gaps], 'covered': 0, 'not_covered': 0, 'rest_errors': 0,
           'window_rest_trades': 0, 'window_missing': [], 'in_gap': [], 'by_gap': collections.defaultdict(lambda: {'rest_in_gap': 0, 'in_file': 0, 'missing': 0})}
    for sym in sorted(syms):
        lst = rest_trades(sym)
        time.sleep(0.12)
        if lst is None:
            res['rest_errors'] += 1
            continue
        ts = [int(x['time']) for x in lst]
        if not ts or min(ts) > lo:
            res['not_covered'] += 1
            continue
        res['covered'] += 1
        for x in lst:
            t = int(x['time'])
            if not lo <= t <= hi:
                continue
            res['window_rest_trades'] += 1
            have = x['execId'] in ids
            if not have:
                res['window_missing'].append({'sym': sym, 't': t, 'id': x['execId'], 'size': x['size'], 'price': x['price']})
            for g in gaps:
                # время сделки биржи на ≈ 110 мс раньше нашего приёма (лаг прихода) — окно пропуска сдвигаем на 0,3 с в обе стороны
                if g[0] - 300 <= t <= g[1]:
                    b = res['by_gap'][f'{g[0]}:{g[1]}']
                    b['rest_in_gap'] += 1
                    b['in_file' if have else 'missing'] += 1
                    res['in_gap'].append({'sym': sym, 't': t, 'in_file': have})
    res['by_gap'] = dict(res['by_gap'])
    res['window_missing_n'] = len(res['window_missing'])
    res['window_missing'] = res['window_missing'][:20]
    res['in_gap'] = res['in_gap'][:20]
    print(json.dumps(res, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
