#!/usr/bin/python3
# TK-525 п.5: полнота L1 по биржам. Бокс — листинг `ls -aR` (с dot-файлами, .ok виден) + невыгруженное на srv (/data/p40l1). Ключ = биржа/символ/сутки.
# exp = ok_l1 + ok_empty + fail + nodir (непересекающиеся классы); ok_empty — .ok без l1 (сырья нет или 0 часов), fail — в fail_raw*.tsv без .ok, nodir — каталога нет нигде.
# Выход /data/p40l1/audit_rc525.json, audit525_fail.txt (сбои поимённо), audit525_ok_no_tr_<ex>.txt, метка AUDIT525_DONE. Запуск: bash srv-tk525-audit.sh (юнит на srv).
import csv,json,os,collections,datetime
OUT="/data/p40l1"; D0=datetime.date(2026,9,17); DAYS=[(D0+datetime.timedelta(i)).isoformat() for i in range(36)]
rows=[r for r in csv.DictReader(open("/root/p40t/out/contract_median.csv",encoding="utf-8")) if r["in_set"]=="1" and r["downloaded"]=="1"]
fails=set()
for f in sorted(x for x in os.listdir(OUT) if x.startswith("fail_raw") and x.endswith(".tsv")):
    if os.path.exists(OUT+"/"+f): fails|={tuple(l.rstrip("\n").split("\t")) for l in open(OUT+"/"+f) if l.strip()}
res={}; flist=[]
for ex in ("okx_futures","bitget_futures","hyperliquid_futures","binance_futures"):
    box={}; cur=None
    for l in open("%s/boxla_%s.txt"%(OUT,ex)):
        l=l.rstrip("\n")
        if l.endswith(":"):
            p=l[:-1].split("/"); cur=tuple(p[3:6]) if len(p)==6 else None
            if cur: box.setdefault(cur,set())
        elif l and cur is not None and l not in (".",".."): box[cur].add(l)
    c=collections.Counter(); nt=[]
    for r in (r for r in rows if r["ex"]==ex):
        for d in DAYS:
            k=(ex,r["sym"],d); fs=set(box.get(k,())); loc="box" if k in box else ""
            sd="%s/%s/%s/%s"%(OUT,ex,r["sym"],d)
            if os.path.isdir(sd): fs|=set(os.listdir(sd)); loc+="+srv" if loc else "srv"
            c["exp"]+=1
            if ".ok" in fs:
                n=sum(f.endswith(".l1.bin.zst") for f in fs); t=sum(f.endswith(".tr.zst") for f in fs)
                c["ok_l1" if n else "ok_empty"]+=1; c["loc_"+loc.replace("+","_")]+=1
                if n: c["l1_24"]+=(n==24); c["tr_eq_l1"]+=(t==n); c["tr_any"]+=(t>0); c["aux"]+=("aux" in fs); c["tr_ok"]+=(".tr.ok" in fs)
                if n and not t: nt.append("%s %s"%(r["sym"],d))
            elif k in fails: c["fail"]+=1; flist.append("\t".join(k)+"\t"+(loc or "нет каталога"))
            else:
                cl="nodir" if not fs else "partial_no_ok"; c[cl]+=1; flist.append("\t".join(k)+"\t"+cl+"\t"+",".join(sorted(fs)[:3]))
    assert c["exp"]==c["ok_l1"]+c["ok_empty"]+c["fail"]+c["nodir"]+c["partial_no_ok"]
    res[ex]=dict(c); open("%s/audit525_ok_no_tr_%s.txt"%(OUT,ex),"w").write("\n".join(nt))
open(OUT+"/audit525_fail.txt","w").write("\n".join(flist)+"\n"); json.dump(res,open(OUT+"/audit_rc525.json","w"),indent=1); print(json.dumps(res,indent=1))
open(OUT+"/AUDIT525_DONE","w").write("ok\n")
