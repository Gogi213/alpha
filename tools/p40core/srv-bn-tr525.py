#!/usr/bin/python3
# TK-525 п.3: сделки (.tr.zst) и aux (mark/OI/liq) Binance из CHFT-parquet бокса chft/binance_futures/<день>/<ЧЧ>/ — L1 Binance строился только из стакана srv, .tr/aux не было.
# Выход: staging /data/p40tr525/binance_futures/<sym>/<день>/{ЧЧ.tr.zst, aux/<ЧЧ>.<имя>.parquet, .tr.ok} -> rsync -aR на box alpha/stakan/p40l1/ (метку .ok L1 не трогаем), после выгрузки staging удаляется.
# Сырьё CHFT не удаляется. Формат .tr/aux — как p40-raw-conv-tk525.py (pq2bin trades; aux/<ЧЧ>.<mark_price|open_interest|liquidations>.parquet).
# Env: JOBS(4) PQ ONLY="SYM/ДЕНЬ,..." LIMIT TAG(bn525). Прогресс /data/p40l1/progress_<TAG>.json, метка /data/p40l1/<TAG>_DONE, сбои fail_<TAG>.tsv.
import csv,os,subprocess,threading,time,json,shutil,datetime
OUT="/data/p40tr525"; ST="/data/p40rc-stage-bn"; PL="/data/p40l1"; BX="alpha/stakan/chft/binance_futures"; BOXL1="alpha/stakan/p40l1/"
PQ=os.environ.get("PQ","/root/pq2bin-tk525/target/release/pq2bin"); JOBS=int(os.environ.get("JOBS","4")); TAG=os.environ.get("TAG","bn525"); LIMIT=int(os.environ.get("LIMIT","0")); MINFREE=float(os.environ.get("MIN_FREE_GB","8"))
RS=["rsync","-a","-e","ssh -c aes128-gcm@openssh.com -o Compression=no"]
D0=datetime.date(2026,9,17); DAYS=[(D0+datetime.timedelta(i)).isoformat() for i in range(36)]
rows=[r for r in csv.DictReader(open("/root/p40t/out/contract_median.csv",encoding="utf-8")) if r["in_set"]=="1" and r["downloaded"]=="1" and r["ex"]=="binance_futures"]
jobs=[(r["sym"],d) for r in rows for d in DAYS]
ONLY=os.environ.get("ONLY")
if ONLY: jobs=[j for j in jobs if j[0]+"/"+j[1] in ONLY.split(",")]
if LIMIT: jobs=jobs[:LIMIT]
DONEF="%s/done_%s.txt"%(PL,TAG)   # возобновление: выгруженные сутки (sym/день) не пересобираются
DN=set(l.strip() for l in open(DONEF)) if os.path.exists(DONEF) else set()
jobs=[j for j in jobs if j[0]+"/"+j[1] not in DN]
total=len(jobs); lock=threading.Lock(); st=dict(done=0,empty=0,fail=0,tr=0,aux=0)
def free(): s=os.statvfs("/data"); return s.f_bavail*s.f_frsize/1e9
def prog():
    d=dict(st,total=total,pct=round(100*(st["done"]+st["empty"])/max(total,1),2),free_gb=round(free(),1),t=time.strftime("%H:%M:%S"))
    json.dump(d,open("%s/progress_%s.json.tmp"%(PL,TAG),"w")); os.replace("%s/progress_%s.json.tmp"%(PL,TAG),"%s/progress_%s.json"%(PL,TAG))
def job(sym,day):
    sd="%s/%s/%s"%(ST,sym,day); od="%s/binance_futures/%s/%s"%(OUT,sym,day)
    shutil.rmtree(sd,ignore_errors=True); shutil.rmtree(od,ignore_errors=True); os.makedirs(sd); os.makedirs(od)
    inc=["--include=*/"]+["--include=%s_%s.parquet"%(sym,k) for k in("trades","mark_price","open_interest","liquidations")]
    rc=subprocess.run(RS+inc+["--exclude=*","--prune-empty-dirs","box:%s/%s/"%(BX,day),sd+"/"],capture_output=True,stdin=subprocess.DEVNULL).returncode
    if rc not in(0,23,24): shutil.rmtree(sd,ignore_errors=True); shutil.rmtree(od,ignore_errors=True); return "fail",0,0,"rsync rc=%d"%rc
    hs=sorted(h for h in os.listdir(sd) if os.path.isdir(sd+"/"+h))
    if not hs: shutil.rmtree(sd,ignore_errors=True); shutil.rmtree(od,ignore_errors=True); return "empty",0,0,"нет файлов на боксе"
    td=od+"/_tr"; ntr=0; naux=0
    if any(os.path.exists("%s/%s/%s_trades.parquet"%(sd,h,sym)) for h in hs):
        rt=subprocess.run([PQ,"trades",sd,"%s_trades.parquet"%sym,td,"1"],capture_output=True)
        if rt.returncode: shutil.rmtree(sd,ignore_errors=True); shutil.rmtree(od,ignore_errors=True); return "fail",0,0,"pq2bin trades rc=%d"%rt.returncode
        if os.path.isdir(td):
            for f in sorted(os.listdir(td)):
                if subprocess.run(["zstd","-q","-3","--rm","-o",od+"/"+f.replace(".bin",".tr.zst"),td+"/"+f]).returncode: return "fail",0,0,"zstd"
                ntr+=1
            shutil.rmtree(td,ignore_errors=True)
    for h in hs:
        for f in sorted(os.listdir(sd+"/"+h)):
            if f.endswith("_trades.parquet"): continue
            os.makedirs(od+"/aux",exist_ok=True); shutil.copy("%s/%s/%s"%(sd,h,f),"%s/aux/%s.%s"%(od,h,f.replace(sym+"_","")))
            naux+=1
    shutil.rmtree(sd,ignore_errors=True)
    sz=sum(os.path.getsize(os.path.join(a,f)) for a,_,fs in os.walk(od) for f in fs)
    open(od+"/.tr.ok","w").write("tr_files=%d aux_files=%d bytes=%d src=chft_box\n"%(ntr,naux,sz))
    ru=subprocess.run(["rsync","-aR","--remove-source-files","-e","ssh -c aes128-gcm@openssh.com -o Compression=no","%s/./binance_futures/%s/%s"%(OUT,sym,day),"box:"+BOXL1],capture_output=True,text=True,stdin=subprocess.DEVNULL)
    if ru.returncode: return "fail",ntr,naux,"upload rc=%d %s"%(ru.returncode,ru.stderr[:120].replace("\n"," "))
    shutil.rmtree(od,ignore_errors=True)
    return "done",ntr,naux,""
it=iter(jobs); il=threading.Lock()
def worker():
    while True:
        if free()<MINFREE: time.sleep(30); continue
        with il: j=next(it,None)
        if j is None: return
        try: r,a,b,msg=job(*j)
        except Exception as e: r,a,b,msg="fail",0,0,str(e)[:150]
        with lock:
            st[r]+=1; st["tr"]+=a; st["aux"]+=b
            if r in("done","empty"): open(DONEF,"a").write("%s/%s\n"%j)
            if r=="fail": open("%s/fail_%s.tsv"%(PL,TAG),"a").write("%s\t%s\t%s\n"%(j[0],j[1],msg))
            if r=="empty": open("%s/empty_%s.tsv"%(PL,TAG),"a").write("%s\t%s\t%s\n"%(j[0],j[1],msg))
            if (st["done"]+st["empty"]+st["fail"])%20==0 or r=="fail": prog()
os.makedirs(ST,exist_ok=True); os.makedirs(OUT,exist_ok=True); prog()
ts=[threading.Thread(target=worker) for _ in range(JOBS)]; [t.start() for t in ts]; [t.join() for t in ts]
prog(); open("%s/%s_DONE"%(PL,TAG),"w").write(time.strftime("%F %T")+"\n")
