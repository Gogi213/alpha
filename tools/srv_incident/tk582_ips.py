import glob, json, collections
dirs = {'binance': '/data/collect/binance/data', 'bybit': '/data/turbo-probe/soak-bybit/data', 'okx': '/data/collect/okx/data', 'bingx': '/data/collect/bingx/data', 'gate': '/data/collect/gate/data'}
for ex, d in dirs.items():
    c = collections.Counter(); ent = collections.Counter(); r = collections.defaultdict(list)
    for f in glob.glob(d + '/events-*.jsonl'):
        for l in open(f):
            if '"connected"' not in l: continue
            try: e = json.loads(l)
            except Exception: continue
            if e.get('ev') == 'connected' and 1794863000000 < e['ts_ms'] < 1794870000000:
                c['.'.join(e['ip'].split('.')[:2])] += 1; ent[e.get('entrance')] += 1; r[e['ip'].split('.')[0]].append(e.get('rtt_ms') or 0)
    print(ex, ent.most_common(2), c.most_common(6), 'rtt med', sorted(sum(r.values(), []))[len(sum(r.values(), []))//2] if r else '')
