#!/usr/bin/env python3
"""TK-388: BNB/USDT для перевода квоты BNB пула SUE в USD на 01.09–14.10: Binance spot 1m klines (open) -> строки по 10 с в формате BBO (sec,bid,ask), bid=ask=open минуты.
Источник не Gate: 10-секундные свечи Gate старше 10000 точек недоступны. Запуск: tk388-bnb.py OUT.csv.gz"""
import sys, io, zipfile, gzip, datetime, urllib.request
out = sys.argv[1]; d = datetime.date(2026, 9, 1); rows = []
while d <= datetime.date(2026, 10, 14):
    u = f'https://data.binance.vision/data/spot/daily/klines/BNBUSDT/1m/BNBUSDT-1m-{d}.zip'
    z = zipfile.ZipFile(io.BytesIO(urllib.request.urlopen(u, timeout=60).read()))
    for line in z.read(z.namelist()[0]).decode().splitlines():
        f = line.split(','); t = int(f[0]); t = t // 10 ** 6 if t > 1e14 else t // 1000 if t > 1e11 else t   # с 2025 spot отдаёт микросекунды
        for s in range(0, 60, 10): rows.append((t + s, f[1]))
    d += datetime.timedelta(days=1)
with gzip.open(out, 'wt') as w:
    for t, p in rows: w.write(f'{t},{p},{p}\n')
print(len(rows), rows[0], rows[-1])
