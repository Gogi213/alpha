import subprocess,datetime,statistics as st
rows=[]
for sa in ("sa13","sa14","sa15"):
    out=subprocess.run(["sadf","-d","/var/log/sysstat/"+sa,"--","-u"],capture_output=True,text=True).stdout
    for ln in out.splitlines():
        p=ln.split(";")
        if len(p)<10 or p[3]!="-1": continue
        t=datetime.datetime.strptime(p[2],"%Y-%m-%d %H:%M:%S UTC")
        if not 8<=t.second<=14: continue
        busy=sum(float(x) for x in p[4:8]); rows.append((t,busy,float(p[8])))
for day in (13,14,15):
    g={"hh:00":[],"mm%5==0":[],"other":[]}
    for t,b,s in rows:
        if t.day!=day: continue
        k="hh:00" if t.minute==0 else ("mm%5==0" if t.minute%5==0 else "other")
        g[k].append((b,s))
    print(day, " | ".join(f"{k}: n={len(v)} busy {st.median(x[0] for x in v):.1f} steal {st.median(x[1] for x in v):.1f}" for k,v in g.items()))
