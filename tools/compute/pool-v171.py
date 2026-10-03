import json,csv,re,datetime as dt,collections,statistics as st,sys
src=open("an.py").read().split("rows=[];SEQ={}")[0]
exec(src)  # perp,T,have,eligible,ms,D0,D1
DA=dt.date(2025,12,1)
alld=[DA+dt.timedelta(n) for n in range((D1-DA).days+1)]
def rank_of_day(d,co):
    # ранг по обороту суток d среди допущенных на d (допуск по возрасту на d; свеча d обязательна)
    c=[]
    for s in perp:
        x=perp[s]; b=x["baseCoin"]
        if b in("BTC","ETH"): continue
        if co:
            if x.get("symbolType") in("stock","ETF","commodity","forex"): continue
        elif b in NONC: continue
        if d not in T[s] or T[s][d]<=0: continue
        if (ms(d)/1000-int(x["launchTime"])/1000)/86400<30: continue
        c.append((T[s][d],s))
    c.sort(reverse=True)
    return {s:i+1 for i,(_,s) in enumerate(c)}
def pool_seq(co,IN=100,OUT=110,N=7):
    rk={d:rank_of_day(d,co) for d in alld}
    up=collections.Counter(); dn=collections.Counter(); pool=set(); res={}
    for d in alld:
        # состав на сутки d — по рангам суток ≤ d-1 (счётчики обновлены до d-1)
        cur=set(pool)
        for s in list(cur):
            if dn[s]>=N or d not in T[s]: cur.discard(s)
        for s in up:
            if up[s]>=N and s not in cur and d in T[s]: cur.add(s)
        pool=cur; res[d]=set(pool)
        # закрываем сутки d: ранги d
        r=rk[d]
        for s in perp:
            k=r.get(s)
            up[s]= up[s]+1 if (k is not None and k<=IN) else 0
            dn[s]= dn[s]+1 if (k is None or k>OUT) else 0
    return res,rk
if __name__=="__main__":
    for co in (0,1):
        res,rk=pool_seq(co)
        ds=[d for d in alld if d>=D0]
        sz=[len(res[d]) for d in ds]; tot=sum(sz); miss=sum(1 for d in ds for s in res[d] if (s,d) not in have)
        ever=set().union(*[res[d] for d in ds])
        print("co",co,"coins",len(ever),"symdays",tot,"missing",miss,"size min/med/max",min(sz),st.median(sz),max(sz))
        if co==0:
            with open("../../docs/findings/pool-v171-2026-10-03.csv","w",newline="") as f:
                f.write("sym,day,in_pool\n")
                for d in ds:
                    for s in sorted(res[d]): f.write(f"{s},{d},1\n")
            pickle=__import__("pickle");pickle.dump((res,rk),open("v171.pkl","wb"))
