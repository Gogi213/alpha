#!/usr/bin/env python3
"""TK-410 П-31 бой: список (монета, файл середины) для tk410-map.py. Монета -> пул cover.csv с файлом середины в mid-all/mid-eth/mid-v4
(chain__пул.csv). Печатает строки 'COIN FILE'; не-EVM и монеты без файла — в stderr (в отчёт охвата). Запуск: calc, cwd /data/tk0393/tk410."""
import csv, os, sys
GEV = os.environ.get('GEV', '/data/tk0393/tk401/gev2all')
for r in csv.DictReader(open('cover.csv', encoding='utf-8')):
    ch, p = r['chain'], r['pool'].lower()
    if ch in ('solana', 'sui', 'tron'): print('nonEVM', r['coin'], ch, file=sys.stderr); continue
    f = None
    for d in ('mid-all', 'mid-eth', 'mid-v4'):
        g = f'{d}/{ch}__{p}.csv'
        if os.path.exists(g) and os.path.getsize(g) > 100: f = g
    if not f: print('nofile', r['coin'], ch, r['source'], file=sys.stderr); continue
    if not os.path.isdir(f'{GEV}/{r["coin"]}'): print('nogev', r['coin'], file=sys.stderr); continue
    print(r['coin'], f)
