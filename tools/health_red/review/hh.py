import json,subprocess,datetime
d=json.load(open("/tmp/v3.json"));T0=d["s0"]
S=json.load(open("/tmp/stall.json"))
st={}
for sa in ("sa14","sa15","sa16"):
    out=subprocess.run(["sadf","-d","/var/log/sysstat/"+sa,"--","-u"],capture_output=True,text=True).stdout
    for ln in out.splitlines():
        p=ln.split(";")
        if len(p)<10 or p[3]!="-1": continue
        t=int(datetime.datetime.strptime(p[2],"%Y-%m-%d %H:%M:%S UTC").replace(tzinfo=datetime.timezone.utc).timestamp())
        st[t]=float(p[8])
print("hour  steal_max(:00-:20) | lag_p99 s / busy_max ms / disk_max ms  in bin :00:00-:00:10 per exchange (bin, bia=binance) ")
for k in range(0,8640):
    t=T0+k*10
    if t%3600: continue
    sm=max([v for tt,v in st.items() if t<=tt<=t+20] or [0])
    row=[]
    for ex in ("binance","bybit","okx","bingx","gate"):
        a=S[ex].get(str(k)) or S[ex].get(str(k+1))
        row.append(f"{ex[:3]} {a[1]/1000:4.1f}/{a[2]/1000:5.1f}/{a[3]/1000:5.1f}" if a else f"{ex[:3]} -")
    print(datetime.datetime.utcfromtimestamp(t).strftime("%d %H:%M"), "%5.1f"%sm, " | ".join(row))
