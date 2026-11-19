# per-gap decomposition for all Binance shards: dark.py <day> <from> <to> <min_gap_s> > jsonl
import sys, glob, json, datetime, collections
day, a, b, mg = sys.argv[1:5]; mg = float(mg) * 1000
def ep(t): return int(datetime.datetime.strptime(day+' '+t, '%Y-%m-%d %H:%M:%S').replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
t0, t1 = ep(a), ep(b)
D = '/data/collect/binance/data'
def ln(s): return 0 if s < 1795000000 else 1
BAD = ('ping_degraded', 'ws_end', 'error', 'closed', 'ws_close')
for f in sorted(glob.glob(D + '/events-*.jsonl')):
    ev = []
    for l in open(f):
        if '"ts_ms"' not in l: continue
        try: e = json.loads(l)
        except Exception: continue
        if t0 - 600000 <= e['ts_ms'] <= t1 + 600000: ev.append(e)
    ev.sort(key=lambda e: e['ts_ms'])
    for g in ev:
        if g['ev'] != 'gap_end' or 'gap_end_ms' not in g or not (t0 <= g['gap_end_ms'] <= t1) or g['gap_total_ms'] < mg: continue
        gs, ge = g['gap_start_ms'], g['gap_end_ms']
        bad = {}
        for e in ev:
            if e['ev'] in BAD and gs - 20000 <= e['ts_ms'] <= ge and e.get('session'):
                bad.setdefault(ln(e['session']), e['ts_ms'])
        t_det = max(bad.values()) - gs if len(bad) == 2 else None
        rej = [e for e in ev if e['ev'] == 'path_rejected' and gs - 1500 <= e['ts_ms'] <= gs + 1500]
        inside = [e for e in ev if gs <= e['ts_ms'] <= ge + 1000]
        c = collections.Counter(e['ev'] for e in inside)
        wait = max([e.get('wait_s', 0) for e in inside if e['ev'] == 'path_reject_capped'] or [0])
        first = None
        for e in inside:
            if t_det is not None and e['ts_ms'] < gs + t_det: continue
            if e['ev'] in ('connected', 'path_rejected_rtt'): first = (e['ts_ms'] - gs, e['ev'], e.get('rtt_ms')); break
            if e['ev'] == 'path_rejected': first = (e['ts_ms'] - gs - 15000, 'rej_probe', e.get('p50_ms')); break
        print(json.dumps({'shard': g['shard'], 'gs': gs, 'ge': ge, 'dur': ge - gs, 't_det_ms': t_det, 'lanes_bad': len(bad), 'rej_at_start': bool(rej), 'rej_p50': rej[0].get('p50_ms') if rej else None,
                          'n_rej': c['path_rejected'], 'n_rejrtt': c['path_rejected_rtt'], 'n_con': c['connected'], 'cap_wait_s': wait, 'n_rcap': c['replace_capped'], 'n_shold': c['storm_hold'], 'n_err': c['error'], 'first': first}))
