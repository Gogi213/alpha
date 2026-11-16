#!/usr/bin/env python3
"""Приёмка выкладки по записи `watch.py`: «до» (окно перед выкладкой, старый процесс) против «после» (новый процесс) по одной бирже.

Сводка: версия, символы с данными за минуту против `pool.rest_eligible`, пул (опрос REST, добавлено / снято, ошибки), шарды без обеих линий,
лаг прихода p50 / p99 (медиана и максимум окон 60 с), queue_lag p99, счётчики потерь (рост sink_*, ring full, rx_clamped), переподключения / таймауты за час.
Запуск: python3 accept.py --ex bybit --deploy-ts <epoch> [--before-min 30] [--dir /data/perf/poolfix-deploy]
"""
import argparse, json, os, statistics as st


def rows(path):
    out = []
    for line in open(path):
        try:
            r = json.loads(line)
        except Exception:
            continue
        if 'err' not in r:
            out.append(r)
    return out


def med(xs):
    xs = [x for x in xs if x is not None]
    return round(st.median(xs), 1) if xs else None


def mx(xs):
    xs = [x for x in xs if x is not None]
    return round(max(xs), 1) if xs else None


def agg(rs):
    g = lambda f: [f(r) for r in rs]  # noqa: E731
    return {'n': len(rs),
            'lag_p50_med': med(g(lambda r: (r.get('lag_in') or {}).get('p50'))), 'lag_p50_max': mx(g(lambda r: (r.get('lag_in') or {}).get('p50'))),
            'lag_p99_med': med(g(lambda r: (r.get('lag_in') or {}).get('p99'))), 'lag_p99_max': mx(g(lambda r: (r.get('lag_in') or {}).get('p99'))),
            'queue_p99_max': mx(g(lambda r: (r.get('queue_lag') or {}).get('p99'))),
            'shard_lag_p99_max': mx(g(lambda r: (r.get('shards') or {}).get('lag_p99_max'))),
            'shards_lt2_max': mx(g(lambda r: (r.get('shards') or {}).get('lt2'))), 'shards_lt1_max': mx(g(lambda r: (r.get('shards') or {}).get('lt1'))),
            'late_1h_max': mx(g(lambda r: max((r.get('late_1h') or {'x': 0}).values()))),
            'cpu_med': med(g(lambda r: r.get('cpu_cores'))), 'lost_1h_s_max': mx(g(lambda r: (r.get('page') or {}).get('lost_1h_s')))}


def delta(rs, k):
    a, b = rs[0]['totals'].get(k), rs[-1]['totals'].get(k)
    return None if a is None or b is None else b - a


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--ex', required=True)
    ap.add_argument('--deploy-ts', type=int, required=True)
    ap.add_argument('--before-min', type=int, default=30)
    ap.add_argument('--dir', default='/data/perf/poolfix-deploy')
    a = ap.parse_args()
    rs = rows(os.path.join(a.dir, f'watch-{a.ex}.jsonl'))
    before = [r for r in rs if a.deploy_ts - a.before_min * 60 <= r['ts'] < a.deploy_ts - 5]
    after = [r for r in rs if r['ts'] >= a.deploy_ts + 90]  # без первых 90 с (прогрев, окно 60 с лага захватывает старт)
    out = {'ex': a.ex, 'before': agg(before) if before else None, 'after': agg(after) if after else None}
    if after:
        last = after[-1]
        p = last.get('pool') or {}
        out['now'] = {'ts': last['ts'], 'ver': last['ver'], 'symbols': last['symbols'], 'shards': last['shards'], 'ws': last['ws'],
                      'pool': {k: p.get(k) for k in ('pool', 'rest_eligible', 'rest_symbols', 'rest_age_s', 'errors', 'last_error', 'not_subscribed', 'missing_at_poll',
                                                      'added_total', 'removed_total', 'spawned_shards', 'remove_blocked')}}
        out['loss_delta_after'] = {k: delta(after, k) for k in ('sink_blocks_dropped', 'sink_errors', 'rx_clamped')}
        out['ring_full_after'] = after[-1].get('ring_full_total')
    if before:
        out['loss_delta_before'] = {k: delta(before, k) for k in ('sink_blocks_dropped', 'sink_errors', 'rx_clamped')}
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
