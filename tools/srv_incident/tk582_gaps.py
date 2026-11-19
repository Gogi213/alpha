# one row per shard gap (gap_end) in window with per-line connection facts: gaps.py <dir> <day> <from> <to> <min_gap_s> [prefixes csv]
import sys, glob, json, datetime, collections
d, day, a, b, mg = sys.argv[1:6]; mg = float(mg) * 1000
pref = sys.argv[6].split(',') if len(sys.argv) > 6 else ['book', 'trade', 'mark']
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
t0, t1 = ep(a), ep(b)
def hms(ms): return datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc).strftime('%H:%M:%S')
def line(s): return 0 if s < 1795000000 else 1
for p in pref:
  for f in sorted(glob.glob(d + '/events-%s*.jsonl' % p)):
    ev = []
    for l in open(f):
        if '"ts_ms"' not in l: continue
        try: e = json.loads(l)
        except Exception: continue
        if t0 - 400000 <= e['ts_ms'] <= t1 + 400000: ev.append(e)
    ev.sort(key=lambda e: e['ts_ms'])
    ip = {}; birth = {}
    for e in ev:
        if e['ev'] == 'connected': ip[e['session']] = e.get('ip'); birth[e['session']] = e['ts_ms']
    for g in ev:
        if g['ev'] != 'gap_end' or not (t0 <= g['gap_end_ms'] <= t1) or g['gap_total_ms'] < mg: continue
        gs, ge = g['gap_start_ms'], g['gap_end_ms']
        out = ['%s %s-%s %4ds' % (g['shard'], hms(gs), hms(ge), (ge-gs)//1000)]
        # sessions active at gs: connected before gs and not ended; use last timeouts per session near gs
        seen = {}
        for e in ev:
            s = e.get('session')
            if e['ev'] in ('timeout', 'ping_degraded', 'ws_end', 'ws_close', 'error') and gs - 30000 <= e['ts_ms'] <= ge + 5000 and s is not None:
                seen.setdefault(s, []).append(e)
        for s in sorted(seen):
            parts = []
            for e in seen[s]:
                t = e.get('tcp') or {}
                if e['ev'] == 'timeout':
                    parts.append('T%+d(%s,ldr%s,un%s,bo%s)' % ((e['ts_ms']-gs)//1000, 'fresh' if e.get('fresh_data') else ('alive' if e.get('data_alive') else 'silent'), t.get('last_data_recv_ms'), t.get('unacked'), t.get('backoff')))
                elif e['ev'] == 'ping_degraded': parts.append('D%+d' % ((e['ts_ms']-gs)//1000))
                else: parts.append('%s%+d(%s)' % (e['ev'], (e['ts_ms']-gs)//1000, str(e.get('how') or e.get('detail') or '')[:30]))
            out.append(' L%d/%s/%s:%s' % (line(s), s % 1000000, ip.get(s, '?'), ' '.join(parts)))
        nc = [e for e in ev if e['ev'] == 'connected' and gs <= e['ts_ms'] <= ge + 3000]
        out.append(' NEW:' + ' '.join('L%d/%s@%+d' % (line(e['session']), e.get('ip'), (e['ts_ms']-gs)//1000) for e in nc))
        oth = [e for e in ev if e['ev'] in ('path_ok', 'path_switch', 'path_rejected', 'path_rejected_rtt', 'storm_hold', 'storm_abort', 'replace_capped', 'replace_deferred', 'connect_wait') and gs <= e['ts_ms'] <= ge + 3000]
        c = collections.Counter(e['ev'] for e in oth)
        out.append(' ' + ' '.join('%s=%d' % kv for kv in c.items()))
        print(''.join(out))
