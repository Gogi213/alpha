"""TK-454: время поста в UTC, год, сессия NY (окна bp: Азия 19-2, Франкфурт 2-3, Лондон 3-8, NY 8-11; время NY с летним) на каждую цепочку
+ первая свеча монеты в минутках Binance (calc; first_candle.csv symbol,ms) -> «монета с историей с 2020» + вердикты по годам.
Запуск: python tools/sancho_trades_meta.py <text.csv> <p1.csv> <first_candle.csv> <out_meta.csv> <out_years.md>"""
import csv, sys, collections, datetime as dt
from zoneinfo import ZoneInfo
text, p1, fc, out, outmd = sys.argv[1:6]
NY = ZoneInfo('America/New_York'); G4 = dt.timezone(dt.timedelta(hours=4))
def sess(h):
    if h >= 19 or h < 2: return 'Азия'
    if h < 3: return 'Франкфурт'
    if h < 8: return 'Лондон'
    if h < 11: return 'Нью-Йорк'
    return 'вне окон'
first = {r['symbol']: int(r['ms']) for r in csv.DictReader(open(fc, encoding='utf-8'))}
best = {}
for r in csv.DictReader(open(p1, encoding='utf-8')):
    k = r['trade_id']
    if k not in best or (r['t0_source'] == 'б' and best[k]['t0_source'] != 'б'): best[k] = r
rows = []
for r in csv.DictReader(open(text, encoding='utf-8')):
    t = dt.datetime.strptime(r['t_post_gmt4'], '%d.%m.%Y %H:%M').replace(tzinfo=G4)
    u = t.astimezone(dt.timezone.utc); n = t.astimezone(NY)
    b = best.get(r['trade_id']); sym = b['symbol'] if b else ''
    f = first.get(sym)
    fd = dt.datetime.fromtimestamp(f / 1000, dt.timezone.utc).strftime('%Y-%m-%d') if f else ''
    since20 = ('да' if f and fd <= '2020-12-31' else 'нет') if f else ('' if not sym else 'нет данных')
    rows.append({'trade_id': r['trade_id'], 't_post_utc': u.strftime('%Y-%m-%d %H:%M'), 'year': u.year, 't_post_ny': n.strftime('%Y-%m-%d %H:%M %Z'),
                 'session': sess(n.hour), 'in_table': int(bool(b)), 'symbol': sym, 'first_candle_utc': fd, 'history_since_2020': since20,
                 'match': b['match'].split(':')[0] + (':' + b['match'].split(':')[1].strip() if ':' in b['match'] else '') if b else ''})
w = csv.DictWriter(open(out, 'w', newline='', encoding='utf-8'), fieldnames=list(rows[0])); w.writeheader(); w.writerows(rows)
def cls(m):
    return m if m in ('совпало', 'не совпало') else ('нельзя проверить' if m.startswith('нельзя') else m)
L = ['| год | цепочек всего | в таблице | совпало | не совпало | нельзя проверить |', '|---|---|---|---|---|---|']
for y in sorted({r['year'] for r in rows}):
    rr = [r for r in rows if r['year'] == y]; tb = [r for r in rr if r['in_table']]
    c = collections.Counter(cls(r['match']) for r in tb)
    L.append(f"| {y} | {len(rr)} | {len(tb)} | {c['совпало']} | {c['не совпало']} | {c['нельзя проверить']} |")
L += ['', '| сессия NY | цепочек | в таблице | совпало | не совпало |', '|---|---|---|---|---|']
for s in ['Азия', 'Франкфурт', 'Лондон', 'Нью-Йорк', 'вне окон']:
    rr = [r for r in rows if r['session'] == s]; tb = [r for r in rr if r['in_table']]; c = collections.Counter(cls(r['match']) for r in tb)
    L.append(f"| {s} | {len(rr)} | {len(tb)} | {c['совпало']} | {c['не совпало']} |")
L += ['', '| монета с историей с 2020 | цепочек в таблице | совпало | не совпало | нельзя проверить |', '|---|---|---|---|---|']
for h in ['да', 'нет', 'нет данных']:
    tb = [r for r in rows if r['in_table'] and r['history_since_2020'] == h]; c = collections.Counter(cls(r['match']) for r in tb)
    L.append(f"| {h} | {len(tb)} | {c['совпало']} | {c['не совпало']} | {c['нельзя проверить']} |")
open(outmd, 'w', encoding='utf-8').write('\n'.join(L) + '\n'); print('\n'.join(L))
