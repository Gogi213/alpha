"""П-41 §8 замер на малом: событийный счёт против сетки 1 с / 100 мс на одной пара-сутки (ноги из L1 бокса).
Уровень d: медиана ln(midA/midB) за 6 ч (до t−60 с), пересчёт раз в минуту — ОДИН на все варианты (изолирует только обнаружение пересечения).
Эпизод (П-40 §3): |d| ≥ θ снизу, взведён (|d|<θ/2 ≥ W=60 с), конец dir·d ≤ 0. Запуск: gridcmp.py exA symA exB symB day θ"""
import sys, subprocess, struct, time, numpy as np
R = "/data/p40l1c/p40l1"
def leg(ex, sym, day):
    T=[];P=[];S=[]
    for h in range(24):
        f = f"{R}/{ex}_futures/{sym}/{day}/{h:02d}.l1.bin.zst"
        try: b = subprocess.run(["zstd","-dc",f],capture_output=True,check=True).stdout
        except Exception: continue
        a = np.frombuffer(b, dtype=np.dtype([("t","<i8"),("u","<i8"),("p","<f8"),("q","<f8"),("k","u1")],align=False)) if len(b)%33==0 else None
        T.append(a["t"]); P.append(a["p"]); S.append(a["k"])
    t=np.concatenate(T); p=np.concatenate(P); k=np.concatenate(S)
    t=np.maximum.accumulate(t); bid=(k&2)>0
    # лучшая цена по сторонам: последнее значение, ffill
    def side(m):
        ts=t[m]; ps=p[m]; return ts, ps
    return t, p, bid
def mid(t, p, bid):
    # ряд mid по событиям: ffill bid/ask
    n=len(t); b=np.full(n,np.nan); a=np.full(n,np.nan)
    b[bid]=p[bid]; a[~bid]=p[~bid]
    import pandas as pd
    b=pd.Series(b).ffill().to_numpy(); a=pd.Series(a).ffill().to_numpy()
    m=(b+a)/2; ok=np.isfinite(m)&(a>=b)
    return t[ok], m[ok]
def detect(ts, d, theta, W=60000):
    """ts возр. (мс), d (bps); -> число эпизодов, список t0, d0, tconv"""
    out=[]; armed_since=None; armed=False; inep=False
    th2=theta/2
    for i in range(len(ts)):
        x=d[i]; a=abs(x)
        if inep:
            if dirn*x<=0: out.append((t0,d0,ts[i]-t0)); inep=False; armed=False; armed_since=None
            continue
        if a<th2:
            if armed_since is None: armed_since=ts[i]
            if ts[i]-armed_since>=W: armed=True
        elif a<theta: pass
        else:
            if armed: inep=True; t0=ts[i]; d0=x; dirn=1.0 if x>0 else -1.0
            armed=False; armed_since=None if a>=theta else armed_since
    return out
exA,symA,exB,symB,day,theta=sys.argv[1:7]; theta=float(theta)
t0=time.time()
tA,mA=mid(*leg(exA,symA,day)); tB,mB=mid(*leg(exB,symB,day)); print("читано",round(time.time()-t0,1),"с; событий",len(tA),len(tB),flush=True)
lo=max(tA[0],tB[0]); hi=min(tA[-1],tB[-1])
def at(t,m,x): i=np.searchsorted(t,x,side="right")-1; return m[np.clip(i,0,len(m)-1)]
# уровень: 1 с сетка, медиана за окно L (на сутки данных окна 6 ч нет — берём всё до t−60 с, минимум 30 мин)
g1=np.arange(lo,hi,1000); r1=np.log(at(tA,mA,g1)/at(tB,mB,g1))
def level_at(x):  # медиана r1 по [lo, x−60 с], пересчёт по минутам
    return None
mins=np.arange(lo+1800000,hi,60000); lev_t=mins; lev=np.array([np.median(r1[:max(1,int((m-60000-lo)//1000))][-21600:]) for m in mins])
def d_at(ts):
    r=np.log(at(tA,mA,ts)/at(tB,mB,ts)); L=lev[np.clip(np.searchsorted(lev_t,ts,side="right")-1,0,len(lev)-1)]
    return (r-L)*1e4
res={}
ev=np.union1d(tA,tB); ev=ev[(ev>=lev_t[0])&(ev<hi)]
for name,ts in (("event",ev),("grid100ms",np.arange(lev_t[0],hi,100)),("grid1s",np.arange(lev_t[0],hi,1000))):
    t1=time.time(); d=d_at(ts); e=detect(ts,d,theta); res[name]=e
    tc=np.array([x[2] for x in e]); d0=np.array([abs(x[1]) for x in e])
    print(f"{name:10s} точек {len(ts):8d} эпизодов {len(e):4d} tconv_med {np.median(tc)/1000 if len(tc) else float('nan'):.2f} с  |d0| med {np.median(d0) if len(d0) else float('nan'):.2f}  счёт {time.time()-t1:.1f} с",flush=True)
ee=np.array([x[0] for x in res["event"]])
for name in ("grid100ms","grid1s"):
    g=np.array([x[0] for x in res[name]]); 
    hit=sum(1 for x in ee if len(g) and np.min(np.abs(g-x))<=2000)
    print(name,"совпали (t0 ±2 с) с событийным:",hit,"из",len(ee),"; лишних",len(g)-hit)
