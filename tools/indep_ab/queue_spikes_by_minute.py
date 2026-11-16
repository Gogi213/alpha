import json,glob,collections,datetime,sys
roots={'bingx':'/data/collect/bingx/data','okx':'/data/collect/okx/data','gate':'/data/collect/gate/data','bybit':'/data/turbo-probe/soak-bybit/data','binance':'/data/collect/binance/data'}
thr=float(sys.argv[1]) if len(sys.argv)>1 else 300.0
hits=collections.defaultdict(lambda:collections.defaultdict(list))  # ex -> window_start_sec -> [(shard,q,lag99)]
tot=collections.Counter()
for ex,r in roots.items():
    for f in glob.glob(r+'/stats-*.jsonl'):
        sh=f.split('stats-')[1][:-6]
        for l in open(f,errors='replace').readlines()[-30000:]:
            try: d=json.loads(l)
            except: continue
            ts=d.get('ts_ms');
            if ts is None: continue
            q=d.get('queue_lag_p99_ms') or 0; lg=d.get('lag_p99_ms') or 0
            tot[ex]+=1
            if q>thr:
                hits[ex][ts//10000*10].append((sh,q,lg))
for ex in hits:
    byhm=collections.Counter()
    for s in hits[ex]:
        t=datetime.datetime.utcfromtimestamp(s)
        byhm[(t.minute)]+=1
    print(ex,'windows(10s) with queue_lag_p99>%g:'%thr,len(hits[ex]),'of',tot[ex],' by minute-of-hour top:',byhm.most_common(8))
print('--- within minute :00, by second-of-minute (10 s window start), queue_lag_p99>thr, count over all exchanges')
c=collections.Counter(); days=collections.defaultdict(set)
for ex in hits:
    for s,v in hits[ex].items():
        t=datetime.datetime.utcfromtimestamp(s)
        if t.minute==0:
            c[t.second]+=1; days[t.strftime('%m-%d %H')].add(ex)
print(sorted(c.items()))
print('hours with spikes (hour -> exchanges):')
for k in sorted(days): print(' ',k,sorted(days[k]))
