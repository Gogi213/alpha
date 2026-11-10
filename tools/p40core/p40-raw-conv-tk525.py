#!/usr/bin/python3
# TK-525 (правка p40-raw-conv.py TK-521): pq2bin tk525; HL — P40_L1_SNAPSEQ=1, Bitget — P40_L1_CARRY=1, везде P40_L1_STRICT=1 (0 часов при непустом входе = fail); REDO=1 (нужен EXF) — пересобрать сутки мимо .ok и uploaded_keys; прогресс progress_raw525.json, метка RAWDONE525, сбои fail_raw525.tsv.
# TK-521: L1+сделки из УЖЕ скачанного сырья CHFT (downloaded=1): okx/bitget/aster/hyperliquid — с бокса alpha/stakan/chft, binance — /data/stakan/chft/binance_futures (только стакан).
# Выход /data/p40l1/<ex>/<sym>/<день>/{ЧЧ.l1.bin.zst, ЧЧ.tr.zst, aux/*.parquet, .ok}. Сырьё НЕ удаляется. Идемпотентно по .ok. Прогресс /data/p40l1/progress_raw525.json, конец RAWDONE.
import csv,os,subprocess,threading,time,json,shutil,datetime,sys
OUT="/data/p40l1"; ST=os.environ.get("ST","/data/p40rc-stage"); PQ=os.environ.get("PQ","/root/pq2bin-tk525/target/release/pq2bin"); REDO=os.environ.get("REDO")=="1"; JOBS=int(os.environ.get("JOBS","4")); MINFREE=float(os.environ.get("MIN_FREE_GB","10")); LIMIT=int(os.environ.get("LIMIT","0"))
BX="alpha/stakan/chft"; SB="/data/stakan/chft/binance_futures"
RS=["rsync","-a","-e","ssh -c aes128-gcm@openssh.com -o Compression=no"]
D0=datetime.date(2026,9,17); DAYS=[(D0+datetime.timedelta(i)).isoformat() for i in range(36)]
rows=[r for r in csv.DictReader(open("/root/p40t/out/contract_median.csv",encoding="utf-8")) if r["in_set"]=="1" and r["downloaded"]=="1" and r["ex"] in("okx_futures","bitget_futures","hyperliquid_futures","binance_futures")]
jobs=[(r["ex"],r["sym"],d) for r in rows for d in DAYS]
EXF=os.environ.get("EXF")
if EXF: jobs=[j for j in jobs if j[0]==EXF]
if LIMIT: jobs=jobs[:LIMIT]
UPK=set(l.strip() for l in open(OUT+"/uploaded_keys.txt")) if os.path.exists(OUT+"/uploaded_keys.txt") else set()
if REDO and not EXF: sys.exit("REDO требует EXF")
jobs=[j for j in jobs if REDO or "/".join(j) not in UPK]
jobs.sort(key=lambda j:j[2]>="2026-10-01")
total=len(jobs); lock=threading.Lock(); st=dict(done=0,skip=0,fail=0,empty=0,raw=0,l1=0)
def free(): s=os.statvfs("/data"); return s.f_bavail*s.f_frsize/1e9
def prog():
    d=dict(st,total=total,pct=round(100*(st["done"]+st["skip"]+st["empty"])/max(total,1),2),free_gb=round(free(),1),t=time.strftime("%H:%M:%S"))
    json.dump(d,open(OUT+"/progress_raw525.json.tmp","w")); os.replace(OUT+"/progress_raw525.json.tmp",OUT+"/progress_raw525.json")
def job(ex,sym,day):
    od="%s/%s/%s/%s"%(OUT,ex,sym,day)
    if REDO: shutil.rmtree(od,ignore_errors=True)
    if os.path.exists(od+"/.ok"): return "skip",0,0
    PE=dict(os.environ,P40_L1_STRICT="1",**({"P40_L1_SNAPSEQ":"1"} if ex=="hyperliquid_futures" else {}),**({"P40_L1_CARRY":"1"} if ex=="bitget_futures" else {}))
    sd="%s/%s/%s/%s"%(ST,ex,sym,day); shutil.rmtree(sd,ignore_errors=True); os.makedirs(sd); os.makedirs(od,exist_ok=True); raw=0
    if ex=="binance_futures":
        src="%s/%s"%(SB,day)
        if not os.path.isdir(src): shutil.rmtree(sd,ignore_errors=True); return "empty",0,0
        rr=subprocess.run([PQ,"l1","book",src,"%s_orderbook.parquet"%sym,od,"1"],capture_output=True,env=PE)
        if rr.returncode: return "fail",0,0
        for h in sorted(os.listdir(src)):
            f="%s/%s/%s_orderbook.parquet"%(src,h,sym)
            if os.path.exists(f): raw+=os.path.getsize(f)
        td=None
    else:
        rc=subprocess.run(RS+["--include=*/","--include=*_%s.parquet"%"*","--exclude=*","--prune-empty-dirs","--include=*/%s_*.parquet"%sym,"box:%s/%s/%s/"%(BX,ex,day),sd+"/"],capture_output=True,stdin=subprocess.DEVNULL).returncode if False else None
        rc=subprocess.run(RS+["--include=*/","--include=%s_*.parquet"%sym,"--exclude=*","--prune-empty-dirs","box:%s/%s/%s/"%(BX,ex,day),sd+"/"],capture_output=True,stdin=subprocess.DEVNULL).returncode
        if rc: shutil.rmtree(sd,ignore_errors=True); return "fail",0,0
        for h in os.listdir(sd):
            for f in os.listdir(sd+"/"+h): raw+=os.path.getsize("%s/%s/%s"%(sd,h,f))
        rr=subprocess.run([PQ,"l1","book",sd,"%s_orderbook.parquet"%sym,od,"1"],capture_output=True,env=PE)
        if rr.returncode: shutil.rmtree(sd,ignore_errors=True); return "fail",raw,0
        td=od+"/_tr"; rt=subprocess.run([PQ,"trades",sd,"%s_trades.parquet"%sym,td,"1"],capture_output=True)
        if rt.returncode: shutil.rmtree(sd,ignore_errors=True); shutil.rmtree(td,ignore_errors=True); return "fail",raw,0
        if os.path.isdir(td):
            for f in os.listdir(td): subprocess.run(["zstd","-q","-3","--rm","-o",od+"/"+f.replace(".bin",".tr.zst"),td+"/"+f])
            shutil.rmtree(td,ignore_errors=True)
        ad=od+"/aux"
        for h in sorted(os.listdir(sd)):
            for f in os.listdir(sd+"/"+h):
                if not (f.endswith("_orderbook.parquet") or f.endswith("_trades.parquet")):
                    os.makedirs(ad,exist_ok=True); shutil.copy(sd+"/"+h+"/"+f,"%s/%s.%s"%(ad,h,f.split("_",1)[1] if False else f.replace(sym+"_","")))
    shutil.rmtree(sd,ignore_errors=True)
    l1=sum(os.path.getsize(od+"/"+f) for f in os.listdir(od) if f.endswith(".l1.bin.zst")); n=sum(1 for f in os.listdir(od) if f.endswith(".l1.bin.zst")); tr=sum(os.path.getsize(od+"/"+f) for f in os.listdir(od) if f.endswith(".tr.zst"))
    open(od+"/.ok","w").write("raw=%d l1=%d tr=%d files=%d src=rawcopy\n"%(raw,l1,tr,n))
    return ("done" if n else "empty"),raw,l1
it=iter(jobs); il=threading.Lock()
def worker():
    while True:
        if free()<MINFREE: time.sleep(30); continue
        with il: j=next(it,None)
        if j is None: return
        try: r,raw,l1=job(*j)
        except Exception as e: r,raw,l1="fail",0,0; open(OUT+"/err_raw525.log","a").write("%s %s\n"%(j,e))
        with lock:
            st[r]+=1; st["raw"]+=raw; st["l1"]+=l1
            if r=="fail": open(OUT+"/fail_raw525.tsv","a").write("%s\t%s\t%s\n"%j)
            if sum(st[k] for k in("done","skip","fail","empty"))%10==0 or r=="fail": prog()
os.makedirs(ST,exist_ok=True); prog()
ts=[threading.Thread(target=worker) for _ in range(JOBS)]; [t.start() for t in ts]; [t.join() for t in ts]
prog(); open(OUT+"/RAWDONE525","w").write(time.strftime("%F %T")+"\n")
