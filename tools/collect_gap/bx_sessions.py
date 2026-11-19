#!/usr/bin/env python3
"""BingX: соединения шарда — по `connected` (ip, rtt, via), `path_ok` (лаг окна проверки), `rotated`/`path_switch`; окно времени.
Запуск: bx_sessions.py <корень> <шард> <с epoch_s> <по epoch_s>"""
import json, sys, time
root, shard, t0, t1 = sys.argv[1], sys.argv[2], int(sys.argv[3]) * 1000, int(sys.argv[4]) * 1000
keys = ('connected', 'path_ok', 'rotated', 'path_switch', 'started', 'path_upgrade', 'ping_degraded', 'entrance_switch', 'path_rejected_rtt', 'gap')
with open(f'{root}/events-{shard}.jsonl') as f:
    for ln in f:
        try:
            e = json.loads(ln)
        except ValueError:
            continue
        ts = e.get('ts_ms', 0)
        if ts < t0 or ts > t1 or e.get('ev') not in keys:
            continue
        ev = e['ev']
        t = time.strftime('%d %H:%M:%S', time.gmtime(ts / 1000))
        if ev == 'connected':
            hs = e.get('hs') or {}
            via = ''
            for k, v in (hs.get('headers', {}) if isinstance(hs, dict) else {}).items():
                if k.lower() == 'via':
                    via = v
            print(t, 'connected s=%s ip=%s rtt=%s via=%s' % (e.get('session'), e.get('ip'), e.get('rtt_ms'), via or str(hs)[:90]))
        else:
            d = {k: v for k, v in e.items() if k not in ('ev', 'ts_ms', 'shard')}
            print(t, ev, json.dumps(d)[:170])
