"""TK-454 §2.7/Q-2: фандинг Binance UM (data.binance.vision monthly fundingRate, сверка .CHECKSUM) за [вход, цель1/стоп по ε=0] для сделок «совпало/не совпало».
Запуск: python tools/sancho_trades_funding.py <p1.csv> <trades.csv> <dir_cache> <out.csv>. Фандинг в bps номинала: лонг платит rate, шорт получает; в R = bps / расстояние до стопа (bps)."""
import csv, sys, io, zipfile, hashlib, urllib.request, datetime as dt, re, os
p1, tr, cache, out = sys.argv[1:5]; os.makedirs(cache, exist_ok=True)
def get(u):
    return urllib.request.urlopen(urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'}), timeout=60).read()
def month(sym, ym):
    fn = f'{sym}-fundingRate-{ym}'; z = os.path.join(cache, fn + '.zip')
    base = f'https://data.binance.vision/data/futures/um/monthly/fundingRate/{sym}/{fn}'
    if not os.path.exists(z): open(z, 'wb').write(get(base + '.zip'))
    want = get(base + '.zip.CHECKSUM').decode().split()[0]; have = hashlib.sha256(open(z, 'rb').read()).hexdigest()
    assert want == have, (fn, want, have)
    rows = list(csv.reader(io.TextIOWrapper(zipfile.ZipFile(z).open(fn + '.csv'), 'utf-8')))
    return [(int(r[0]), float(r[2])) for r in rows if r and r[0].isdigit()]
trd = {}
for r in csv.DictReader(open(tr, encoding='utf-8')): trd.setdefault(r['trade_id'], r)
best = {}
for r in csv.DictReader(open(p1, encoding='utf-8')):
    k = r['trade_id']
    if k not in best or (r['t0_source'] == 'б' and best[k]['t0_source'] != 'б'): best[k] = r
res = []
for k, r in best.items():
    if not r['match'].startswith(('совпало', 'не совпало')): continue
    t0 = int(r['entry_min'].split('|')[0]); t1 = int(re.search(r'eps0:\w+@(\d+)', r['p1']).group(1))
    sym = r['symbol'].replace('PEPEUSDT', '1000PEPEUSDT') if r['symbol'] == 'PEPEUSDT' else r['symbol']
    ms, rates = set(), []
    d = dt.datetime.fromtimestamp(t0 / 1000, dt.timezone.utc).replace(day=1)
    while d.timestamp() * 1000 <= t1:
        rates += month(sym, d.strftime('%Y-%m')); d = (d.replace(day=28) + dt.timedelta(days=5)).replace(day=1)
    n = [x for t, x in rates if t0 < t <= t1]; s = sum(n)
    t = trd[k]; e, st = float(t['entry']), float(t['stop']); dist = abs(e - st) / e * 1e4
    sign = -1 if r['dir'] == 'long' else 1
    res.append({'trade_id': k, 'symbol': sym, 'dir': r['dir'], 't_entry_ms': t0, 't_exit_ms': t1, 'hours': round((t1 - t0) / 3.6e6, 1), 'n_funding': len(n),
                'funding_bps_received': round(sign * s * 1e4, 2), 'stop_dist_bps': round(dist, 1), 'funding_R': round(sign * s * 1e4 / dist, 3)})
w = csv.DictWriter(open(out, 'w', newline='', encoding='utf-8'), fieldnames=list(res[0])); w.writeheader(); w.writerows(res)
for x in res: print(x)
