#!/usr/bin/env python3
"""Запись health боевых коллекторов раз в N с (по строке JSON на биржу) — «до» и «после» выкладки: лаг прихода, потери, соединения, пул.

Каждая строка: ts, exchange, версия, символы (подписано / с данными за минуту), счётчики потерь (sink_*, ring full, rx_clamped),
ws (соединения, провалы, переподключения, таймауты за час), лаг прихода p50/p99 (окно 60 с, общий и по видам), queue_lag, late_1h по видам,
шарды (сколько без обеих линий, максимум lag_in_p99), раздел `pool` (если есть), строка биржи с :8088 (`lost_1h_s`, `ok`, `live_bad`).
Запуск: python3 watch.py --out /data/perf/poolfix-deploy [--every 30]; под systemd-run (Nice=19).
"""
import argparse, json, os, time, urllib.request

HEALTH = {
    'binance': '/data/turbo-probe/soak-binance/health.json',
    'bybit': '/data/turbo-probe/soak-bybit/health.json',
    'okx': '/data/collect/okx/health.json',
    'bingx': '/data/collect/bingx/health.json',
    'gate': '/data/collect/gate/health.json',
}


def page_rows():
    try:
        with urllib.request.urlopen('http://127.0.0.1:8088/api/v3', timeout=6) as r:
            d = json.loads(r.read())
        return {x['id']: {k: x.get(k) for k in ('ok', 'live_bad', 'late_bad', 'lost_1h_s')} for x in d['rows']}
    except Exception as e:  # noqa
        return {'_err': str(e)[:80]}


def snap(ex, h, rows):
    t = h.get('totals', {})
    ws = h.get('ws', {})
    sh = h.get('shards', [])
    fresh = (h.get('fresh') or {}).get('kinds', {})
    bp = h.get('backpressure', {})
    return {
        'ts': int(time.time()), 'ex': ex, 'ver': (h.get('process') or {}).get('version'), 'age_ms': int(time.time() * 1000) - h.get('ts_ms', 0),
        'started_ms': h.get('started_ms'), 'symbols': h.get('symbols'),
        'totals': {k: t.get(k) for k in ('records', 'dups', 'sink_blocks_dropped', 'sink_errors', 'rx_clamped', 'raw')},
        'ring_full_total': (bp.get('ring') or {}).get('full_events_total'), 'ring_blocked_ms': (bp.get('ring') or {}).get('blocked_ms_total'),
        'ws': {k: ws.get(k) for k in ('connections', 'gaps_1h', 'gaps_24h', 'reconnects_1h', 'timeouts_1h', 'timeouts_silent_1h', 'path_switches_1h')},
        'lag_in': h.get('lag_in_ms'), 'lag_in_kind': {k: {'p50': v.get('p50'), 'p99': v.get('p99')} for k, v in (h.get('lag_in_by_kind_ms') or {}).items()},
        'queue_lag': bp.get('queue_lag_ms'), 'late_1h': {k: v.get('late_1h') for k, v in fresh.items()},
        'shards': {'n': len(sh), 'lt2': sum(1 for s in sh if s.get('connected', 0) < 2), 'lt1': sum(1 for s in sh if s.get('connected', 0) < 1),
                   'lag_p99_max': max([s.get('lag_in_p99_ms') or 0 for s in sh] or [0])},
        'cpu_cores': (h.get('process') or {}).get('cpu_cores'), 'pool': h.get('pool'), 'page': rows.get(ex),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--out', required=True)
    ap.add_argument('--every', type=int, default=30)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    while True:
        t0 = time.time()
        rows = page_rows()
        for ex, p in HEALTH.items():
            try:
                h = json.load(open(p))
                line = snap(ex, h, rows)
            except Exception as e:  # noqa
                line = {'ts': int(time.time()), 'ex': ex, 'err': str(e)[:100]}
            with open(os.path.join(a.out, f'watch-{ex}.jsonl'), 'a') as f:
                f.write(json.dumps(line, separators=(',', ':')) + '\n')
        time.sleep(max(1, a.every - (time.time() - t0)))


if __name__ == '__main__':
    main()
