import sys, json, collections, datetime, statistics
# TK-582 v2: оценка снятия потерь итоговым правилом на записанных паузах 16.11 (dark-all.jsonl; события — /data/collect/binance/data).
# Правило: шард тёмный = пауза данных + ни одна линия не несёт; тёмный шард проходит режим «стоянка», потолки и RTT (первое соединение принято как есть);
# молчащая линия портится за 4–7 с; медленный единственный источник принимается (rej_at_start). Паузы < 4 с правило не трогает.
D = '/data/collect/binance/data'
rows = [json.loads(l) for l in open(sys.argv[1])]
day = '2026-11-16'
def ep(t): return int(datetime.datetime.strptime(day + ' ' + t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)
EPI = [('E1', '21:05:50', '21:06:49'), ('E2', '21:15:01', '21:16:03'), ('E3', '21:25:14', '21:25:53'), ('E4', '21:33:49', '21:34:52'), ('E5', '21:47:48', '21:48:17'), ('E6', '22:03:42', '22:04:09'), ('E7', '22:15:46', '22:16:14')]
STP = [('S1', '21:18:32', '21:21:57'), ('S2', '21:49:11', '21:53:04'), ('S3', '22:19:46', '22:21:13')]
def win(r):
    for n, a, b in STP:
        if ep(a) - 60000 <= r['gs'] <= ep(b): return n
    for n, a, b in EPI:
        if ep(a) - 5000 <= r['gs'] <= ep(b) + 5000: return n
    return 'other'
def cls(w): return 'S' if w.startswith('S') else ('E' if w.startswith('E') else 'other')
cache = {}
def blocks(sh):
    if sh not in cache:
        L = []
        for l in open('%s/events-%s.jsonl' % (D, sh)):
            if not any(k in l for k in ('storm_hold', 'replace_capped', 'path_reject_capped')): continue
            try: e = json.loads(l)
            except Exception: continue
            L.append(e['ts_ms'])
        cache[sh] = L
    return cache[sh]
def grp(r): return 'trade' if r['shard'].startswith('trade') else ('mark' if r['shard'].startswith('mark') else 'book')
q = lambda X, p: sorted(X)[min(len(X) - 1, int(len(X) * p))]
for r in rows:
    r['blk'] = bool([t for t in blocks(r['shard']) if r['gs'] - 300000 <= t <= r['ge']])
    r['w'] = win(r); r['g'] = grp(r)
# время замены после обнаружения (обе линии помечены → первый кадр новой линии): свободные паузы с известным обнаружением; прежний путь замены, RTT-отбраковки в него входят (консервативно)
free = [r['dur'] / 1000 - r['t_det_ms'] / 1000 for r in rows if not r['blk'] and r['t_det_ms'] is not None and r['t_det_ms'] >= 0 and r['dur'] / 1000 > r['t_det_ms'] / 1000]
T50, T90 = statistics.median(free), q(free, .9)
print('replacement time after detection, free pauses with known detection: n=%d median %.1f s p90 %.1f s' % (len(free), T50, T90))
# шард тёмный не раньше паузы данных `in_gap`: порог 3 с (GAP_TIMEOUT_MS) + такт надзора 0,5 с
DARK_MIN = 3.5
def new_dur(r, td_new, T):
    d = r['dur'] / 1000
    if d < 4: return d, 'short'
    if r['rej_at_start']: return 0.0, 'sole'
    td = max(0.0, r['t_det_ms'] / 1000) if r['t_det_ms'] is not None else None
    tdn = max(DARK_MIN, min(td, td_new)) if td is not None else max(DARK_MIN, td_new)
    if (not r['blk']) and td is not None:
        return d - max(0.0, min(td - tdn, d)), 'free'
    return min(d, tdn + T), 'blocked' if r['blk'] else 'free_notd'
tab = collections.defaultdict(collections.Counter)
for r in rows:
    d = r['dur'] / 1000
    nb, kb = new_dur(r, 4, T50); nw, kw = new_dur(r, 7, T90)
    for key in ((cls(r['w']), r['g']), (cls(r['w']), 'all'), ('ALL', r['g']), ('ALL', 'all')):
        t = tab[key]; t['n'] += 1; t['dark'] += d; t['best'] += d - nb; t['worst'] += d - nw
        if kb == 'sole': t['nsole'] += 1; t['sole'] += d
        if r['blk']: t['nblk'] += 1
print('class group n dark_s | blocked n | sole-source n s | saved best (detect 4 s, replace median) / worst (7 s, p90)')
for k in sorted(tab):
    if k[1] != 'all' and k[0] != 'ALL': continue
    t = tab[k]
    print('%-5s %-5s %3d %6.0f | %3d | %2d %5.0f | %5.0f (%2.0f%%) / %5.0f (%2.0f%%)' % (k[0], k[1], t['n'], t['dark'], t['nblk'], t['nsole'], t['sole'], t['best'], 100 * t['best'] / t['dark'], t['worst'], 100 * t['worst'] / t['dark']))
print('by window (all groups)')
bw = collections.defaultdict(collections.Counter)
for r in rows:
    d = r['dur'] / 1000
    nb, _ = new_dur(r, 4, T50); nw, _ = new_dur(r, 7, T90)
    t = bw[r['w']]; t['n'] += 1; t['dark'] += d; t['best'] += d - nb; t['worst'] += d - nw
for w in sorted(bw):
    t = bw[w]; print('%-6s n=%3d dark %5.0f s saved %5.0f (%2.0f%%) / %5.0f (%2.0f%%)' % (w, t['n'], t['dark'], t['best'], 100 * t['best'] / t['dark'], t['worst'], 100 * t['worst'] / t['dark']))
print('mark00 pauses >= 8 s in episodes (model of the final rule; v1 AB 4 s/7 s for comparison)')
tot = collections.Counter()
for r in rows:
    if r['g'] != 'mark' or not r['w'].startswith('E') or r['dur'] < 8000: continue
    d = r['dur'] / 1000; td = max(0.0, r['t_det_ms'] / 1000) if r['t_det_ms'] is not None else None
    f = r['first']; ft = f[0] / 1000 if f else None
    # в эпизоде первое подключение после обнаружения — фактическое время (рукопожатие, отбракованное по RTT) + 2 с до кадра; обнаружение 4/7 с сдвигает попытки раньше
    def end(td_new, T):
        tdn = max(DARK_MIN, min(td, td_new)) if td is not None else max(DARK_MIN, td_new)
        if td is not None and ft is not None and f[1] != 'connected': return min(d, tdn + max(0.0, ft - td) + 2.0)
        return min(d, tdn + T)
    nb, nw = end(4, T50), end(7, T90)
    tot['dur'] += d; tot['best'] += d - nb; tot['worst'] += d - nw
    print('%s %-3s %5.0f td=%s first=%s -> end %.0f/%.0f s, saved %.0f/%.0f' % (datetime.datetime.fromtimestamp(r['gs'] / 1000, datetime.timezone.utc).strftime('%H:%M:%S'), r['w'], d, ('%.0f' % td) if td is not None else '-', ('+%.0f %s' % (ft, f[1])) if f else '-', nb, nw, d - nb, d - nw))
print({k: round(v) for k, v in tot.items()})
