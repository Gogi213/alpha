import subprocess,glob,sys,collections,numpy as np,datetime
H=int(sys.argv[1]); root='/data/collect/bingx/data'
day=datetime.datetime.fromtimestamp(H,datetime.timezone.utc).strftime('%Y-%m-%d')
res={}
for f in sorted(glob.glob(f'{root}/{day}/trade0?-*.tsv.zst')):
    sh=f.split('/')[-1][:7]
    p=subprocess.Popen(['zstd','-dc',f],stdout=subprocess.PIPE,bufsize=1<<22); rx=0
    for line in p.stdout:
        a=line.split(b'\t',5)
        try: rx+=int(a[1])
        except: continue
        if len(a)>=5 and a[0]==b't' and H-3<=rx/1e9<H+8:
            res.setdefault(sh,[]).append((rx/1e9-H,int(a[2])+int(a[3]),a[4].decode()))  # lag = lag_e+ET = rx_ms - T
    p.wait()
for sh,rows in sorted(res.items()):
    print(sh,'rows',len(rows))
    for s in range(-2,7):
        x=[l for t,l,sy in rows if s<=t<s+1]
        if x: print('  %+d s n=%3d p50 %5.0f p90 %5.0f max %5.0f'%(s,len(x),np.percentile(x,50),np.percentile(x,90),max(x)))
    x=[(l,sy) for t,l,sy in rows if 0<=t<5 and l>200]
    c=collections.Counter(sy for l,sy in x)
    print('  rows with lag>200ms in 0..5s:',len(x),'symbols:',c.most_common(6))
