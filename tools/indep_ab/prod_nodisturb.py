"""Prod collector stats (10 s windows) before vs during the independent clients: medians of lag p50, queue lag p99, recvq max, ring_full, max_gap.
Usage: prod_nodisturb.py <stats_dir> <t_start_clients_epoch> <t_end_epoch> [shard-prefixes]"""
import sys,glob,json,statistics,datetime
root,t0,t1=sys.argv[1],int(sys.argv[2]),int(sys.argv[3])
bef=(t0-2400,t0-60); dur=(t0+600,t1)
def fmt(x): return '%.1f'%x if x is not None else '-'
print('shard | window | n | lag_p50 med | lag_p99 med | queue_lag_p99 med | queue_lag_p99 p95 | recvq_max med | ring_full sum | max_gap_ms med')
for f in sorted(glob.glob(root+'/stats-*.jsonl')):
    sh=f.split('stats-')[1][:-6]
    rows=[]
    for l in open(f,errors='replace').readlines()[-6000:]:
        try:d=json.loads(l)
        except: continue
        if d.get('ts_ms') is None: continue
        rows.append(d)
    for name,(a,b) in (('before',bef),('during',dur)):
        w=[d for d in rows if a<=d['ts_ms']/1000<b and (d['ts_ms']//1000)%3600>120 and (d['ts_ms']//1000)%300>20]  # skip :00 and 5-min marks (own comparison is hourly)
        def med(k):
            v=[d[k] for d in w if d.get(k) is not None]; return statistics.median(v) if v else None
        q=[d['queue_lag_p99_ms'] for d in w if d.get('queue_lag_p99_ms') is not None]
        q95=sorted(q)[int(len(q)*0.95)] if q else None
        print(sh,'|',name,'|',len(w),'|',fmt(med('lag_p50_ms')),'|',fmt(med('lag_p99_ms')),'|',fmt(med('queue_lag_p99_ms')),'|',fmt(q95),'|',fmt(med('recvq_max')),'|',sum(d.get('ring_full') or 0 for d in w),'|',fmt(med('max_gap_ms')))
