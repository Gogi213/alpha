# per-gap story: gaps2.py <dir> <day> <from> <to> <min_gap_s> [prefixes]
import sys, glob, json, datetime, collections
d, day, a, b, mg = sys.argv[1:6]; mg = float(mg) * 1000
pref = sys.argv[6].split(',') if len(sys.argv) > 6 else ['book', 'trade', 'mark']
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
t0, t1 = ep(a), ep(b)
def hms(ms): return datetime.datetime.fromtimestamp(ms/1000, datetime.timezone.utc).strftime('%H:%M:%S')
def ln(s): return 0 if s < 1795000000 else 1
NOISE = ('slow_pong', 'replace_forced', 'subscribed', 'gap', 'started', 'stopped')
for p in pref:
  for f in sorted(glob.glob(d + '/events-%s*.jsonl' % p)):
    ev = []
    for l in open(f):
        if '"ts_ms"' not in l: continue
        try: e = json.loads(l)
        except Exception: continue
        if t0 - 400000 <= e['ts_ms'] <= t1 + 400000: ev.append(e)
    ev.sort(key=lambda e: e['ts_ms'])
    for g in ev:
        if g['ev'] != 'gap_end' or not (t0 <= g['gap_end_ms'] <= t1) or g['gap_total_ms'] < mg: continue
        gs, ge = g['gap_start_ms'], g['gap_end_ms']
        toks = []
        for e in ev:
            if not (gs - 20000 <= e['ts_ms'] <= ge + 2000): continue
            k = e['ev']; s = e.get('session'); r = '%+d' % ((e['ts_ms'] - gs) // 1000)
            if k in NOISE or k == 'gap_end': continue
            L = 'L%d' % ln(s) if s else ''
            if k == 'timeout':
                t = e.get('tcp') or {}
                if e.get('fresh_data') and not t.get('unacked'): continue
                toks.append('T%s%s(%s,ldr%.1f,un%s,bo%s)' % (L, r, 'fresh' if e.get('fresh_data') else ('alive' if e.get('data_alive') else 'silent'), (t.get('last_data_recv_ms') or 0)/1000, t.get('unacked'), t.get('backoff')))
            elif k == 'ping_degraded': toks.append('DEG%s%s' % (L, r))
            elif k == 'connected': toks.append('CON%s%s' % (L, r))
            elif k == 'path_rejected': toks.append('REJ%s%s(p50=%s)' % (L, r, e.get('p50_ms')))
            elif k == 'path_rejected_rtt': toks.append('REJrtt%s%s(%s)' % (L, r, int(e.get('rtt_ms') or 0)))
            elif k == 'path_reject_capped': toks.append('CAP%s%s(n%s,w%ss)' % (L, r, e.get('rejects'), e.get('wait_s')))
            elif k == 'replace_capped': toks.append('RCAP%s%s' % (L, r))
            elif k == 'replace_deferred': toks.append('RDEF%s%s' % (L, r))
            elif k == 'storm_hold': toks.append('SHOLD%s%s' % (L, r))
            elif k == 'storm_abort': toks.append('SABORT%s%s' % (L, r))
            elif k == 'path_ok': toks.append('OK%s%s' % (L, r))
            elif k == 'path_switch': toks.append('SW%s%s' % (L, r))
            elif k in ('ws_end', 'error', 'closed', 'ws_close'): toks.append('%s%s%s(%s)' % (k.upper(), L, r, str(e.get('how') or e.get('err') or '')[:24].replace(' ', '_')))
            else: toks.append('%s%s%s' % (k, L, r))
        print('%s %s-%s %ds | %s' % (g['shard'], hms(gs), hms(ge), (ge-gs)//1000, ' '.join(toks)))
