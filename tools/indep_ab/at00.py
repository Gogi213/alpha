import json,sys,datetime,glob
hour=sys.argv[1]  # e.g. 2026-11-16T16
t0=int(datetime.datetime.strptime(hour,'%Y-%m-%dT%H').replace(tzinfo=datetime.timezone.utc).timestamp())
roots={'bingx':'/data/collect/bingx/data','okx':'/data/collect/okx/data','bybit':'/data/turbo-probe/soak-bybit/data','binance':'/data/collect/binance/data','gate':'/data/collect/gate/data'}
for ex,r in roots.items():
    for f in sorted(glob.glob(r+'/stats-*.jsonl')):
        sh=f.split('stats-')[1][:-6]
        if not (sh.startswith('trade') or sh.startswith('book00') or sh=='trade00'): continue
        if sh not in ('trade00','trade03','book00') : continue
        rows=[]
        for l in open(f,errors='replace').readlines()[-3000:]:
            try:d=json.loads(l)
            except: continue
            ts=d.get('ts_ms',0)//1000
            if t0-30<=ts<=t0+90: rows.append(d)
        print(ex,sh,[(datetime.datetime.utcfromtimestamp(d['ts_ms']//1000).strftime('%M:%S'),d.get('queue_lag_p99_ms'),d.get('lag_p99_ms'),d.get('busy_max_us'),d.get('max_gap_ms'),d.get('msgs'),d.get('disk_write_max_us'),d.get('recvq_max')) for d in rows])
