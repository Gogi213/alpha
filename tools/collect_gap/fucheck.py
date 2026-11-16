#!/usr/bin/env python3
"""Снимок приёмки доработок коллекторов (G-022, выкладка follow-ups v2): одна биржа — одним JSON из health, строки страницы и каталога данных.

Поля: версия и возраст процесса; символы с данными за минуту / REST-состав / пул / `not_subscribed` / `rest_checked`; пул (`over_cap`, `max_shard_load`, `rates_tracked`,
`spawned_shards`, ошибки опроса); `backfill` целиком; `storm`; `connect_budget`; паузы и переподключения; лаг прихода; счётчики потерь записи; шарды с < 2 линий;
строка страницы (`ok`, `budget_bad`, `lost_ids_1h`, `backfill_lost_1h`); `pool-state.json` (возраст, число меток и нагрузок). Запуск: python3 fucheck.py <биржа>
"""
import json, os, sys, time, urllib.request

HEALTH = {'binance': '/data/turbo-probe/soak-binance/health.json', 'bybit': '/data/turbo-probe/soak-bybit/health.json', 'okx': '/data/collect/okx/health.json',
          'bingx': '/data/collect/bingx/health.json', 'gate': '/data/collect/gate/health.json'}
ROOT = {'binance': '/data/collect/binance/data', 'bybit': '/data/turbo-probe/soak-bybit/data', 'okx': '/data/collect/okx/data', 'bingx': '/data/collect/bingx/data', 'gate': '/data/collect/gate/data'}


def main():
    ex = sys.argv[1]
    h = json.load(open(HEALTH[ex]))
    now = time.time()
    sym, ws, pool, t, bp = h.get('symbols') or {}, h.get('ws') or {}, h.get('pool') or {}, h.get('totals') or {}, h.get('backpressure') or {}
    sh = h.get('shards') or []
    try:
        d = json.load(urllib.request.urlopen('http://127.0.0.1:8088/api/v3', timeout=15))
        row = next((r for r in d['rows'] if r['id'] == ex), {})
        page = {k: row.get(k) for k in ('ok', 'live_bad', 'late_bad', 'budget_bad', 'lost_1h_s', 'lost_ids_1h', 'backfill_lost_1h')}
    except Exception as e:  # noqa
        page = {'err': str(e)[:80]}
    ps = os.path.join(ROOT[ex], 'pool-state.json')
    pst = None
    if os.path.exists(ps):
        try:
            j = json.load(open(ps))
            pst = {'age_s': round(now - os.path.getmtime(ps)), 'keys': list(j.keys()), 'last_above': len(j.get('last_above') or {}), 'rates': len(j.get('rates') or {})}
        except Exception as e:  # noqa
            pst = {'err': str(e)[:80]}
    out = {
        'ex': ex, 'utc': time.strftime('%H:%M:%S', time.gmtime(now)), 'ver': (h.get('process') or {}).get('version'), 'up_s': round(now - (h.get('started_ms') or 0) / 1000),
        'symbols': {k: sym.get(k) for k in ('subscribed', 'with_data_1m', 'not_subscribed', 'not_subscribed_qv_usd', 'rest_checked', 'not_in_layout', 'book_window_s')},
        'not_subscribed_top': sym.get('not_subscribed_top'),
        'pool': {k: pool.get(k) for k in ('pool', 'rest_eligible', 'rest_age_s', 'errors', 'last_error', 'added_total', 'removed_total', 'spawned_shards', 'over_cap',
                                          'max_shard_load', 'max_shard', 'rates_tracked', 'held_from_previous_run', 'missing_at_poll')},
        'backfill': h.get('backfill'), 'storm': h.get('storm'), 'connect_budget': h.get('connect_budget'),
        'ws': {k: ws.get(k) for k in ('connections', 'gaps_1h', 'gaps_1h_nontrade', 'reconnects_1h', 'timeouts_1h', 'timeouts_silent_1h', 'path_switches_1h')},
        'lag_in': h.get('lag_in_ms'), 'queue_lag': bp.get('queue_lag_ms'),
        'loss': {k: t.get(k) for k in ('records', 'sink_blocks_dropped', 'sink_errors', 'rx_clamped')}, 'ring_full': (bp.get('ring') or {}).get('full_events_total'),
        'shards': {'n': len(sh), 'lt2': sum(1 for s in sh if s.get('connected', 0) < 2), 'lt1': sum(1 for s in sh if s.get('connected', 0) < 1)},
        'page': page, 'pool_state': pst,
    }
    print(json.dumps(out, ensure_ascii=False, indent=1))


if __name__ == '__main__':
    main()
