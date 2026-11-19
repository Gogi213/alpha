#!/usr/bin/env python3
"""Сбросы соединений и «тихие» таймауты всех боевых коллекторов с момента --since (epoch с): события `ws_end` и `timeout` из `events-*.jsonl`.

Строка: время UTC, биржа, шард, событие, причина (`how` / `kind`), возраст сессии, TCP-счётчики (retrans, backoff, rtt_ms), вход. Для сверки всплесков между биржами.
Запуск: python3 resets.py <since epoch>
"""
import glob, json, sys, time

ROOTS = {'binance': '/data/collect/binance/data', 'bybit': '/data/turbo-probe/soak-bybit/data', 'okx': '/data/collect/okx/data',
         'bingx': '/data/collect/bingx/data', 'gate': '/data/collect/gate/data'}
t0 = int(sys.argv[1])
out = []
for ex, root in ROOTS.items():
    for f in glob.glob(root + '/events-*.jsonl'):
        for line in open(f):
            if '"ws_end"' not in line and '"timeout"' not in line:
                continue
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = (d.get('ts_ms') or 0) / 1000
            if ts < t0 or d.get('ev') not in ('ws_end', 'timeout'):
                continue
            tcp, dg = d.get('tcp') or {}, d.get('diag') or {}
            out.append((ts, ex, d.get('shard'), d['ev'], d.get('how') or d.get('kind'), dg.get('age_s'), tcp.get('retrans'), tcp.get('backoff'), tcp.get('rtt_ms'), d.get('entrance')))
out.sort(key=lambda o: o[0])
for o in out:
    print(time.strftime('%H:%M:%S', time.gmtime(o[0])), *o[1:])
