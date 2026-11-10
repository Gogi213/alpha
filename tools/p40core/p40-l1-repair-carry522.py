#!/usr/bin/python3
# TK-522 (E-021): пересборка L1-суток CHFT с переносом книги через часы и посевом часами прошлых суток (P40_L1_CARRY=1, P40_L1_SEED).
# Часы без снимка в сырье (OKX неликвидные) собираются от книги прошлого часа, а не из набора тронутых дельтами уровней.
# usage: p40-l1-repair-carry522.py <ex>/<sym>/<day> ...   выход $OUT/<ex>/<sym>/<day>/{ЧЧ.l1.bin.zst, .ok}; сделки не трогаем (L1-кэш сделок прежний).
import os,sys,subprocess,shutil,datetime,concurrent.futures as cf
env=dict(l.strip().split("=",1) for l in open("/root/.chft.env") if "=" in l); K=env["CRYPTOHFTDATA_API_KEY"]
A="https://api.cryptohftdata.com"; ST="/data/p40d-stage/carry522"; OUT=os.environ.get("OUT","/data/p40l1-carry"); PQ=os.environ.get("PQ","/root/pq2bin-tk522e/target/release/pq2bin")
SEED_HOURS=[int(x) for x in os.environ.get("SEED_HOURS","12,13,14,15,16,17,18,19,20,21,22,23").split(",")]
def get(f,dst):
    u=subprocess.run(["curl","-s","-o","/dev/null","-m","60","-w","%{http_code} %{redirect_url}","-H","X-API-Key: "+K,A+"/v1/download?file="+f],capture_output=True,text=True).stdout.split(" ",1)
    if u[0]=="404": return 404
    if u[0]!="302": return -1
    os.makedirs(os.path.dirname(dst),exist_ok=True)
    return 200 if subprocess.run(["curl","-s","--fail","-m","900","-o",dst,u[1].strip()]).returncode==0 else -1
for key in sys.argv[1:]:
    ex,sym,day=key.split("/"); sd=f"{ST}/{key}"; od=f"{OUT}/{key}"
    prev=(datetime.date.fromisoformat(day)-datetime.timedelta(days=1)).isoformat()
    shutil.rmtree(sd,ignore_errors=True); shutil.rmtree(od,ignore_errors=True); os.makedirs(od)
    jobs=[(f"{ex}/{day}/{h:02d}/{sym}_orderbook.parquet",f"{sd}/{h:02d}/{sym}_orderbook.parquet") for h in range(24)]
    sj=[(f"{ex}/{prev}/{h:02d}/{sym}_orderbook.parquet",f"{sd}/seed/{h:02d}.parquet") for h in SEED_HOURS]
    with cf.ThreadPoolExecutor(16) as tp: rs=list(tp.map(lambda j:get(*j),jobs+sj))
    ok=rs[:24].count(200); bad=rs.count(-1); seeds=[j[1] for j,r in zip(sj,rs[24:]) if r==200]
    e=dict(os.environ,P40_L1_CARRY="1",P40_L1_SEED=",".join(seeds))
    rc=subprocess.run([PQ,"l1","book",sd,f"{sym}_orderbook.parquet",od,"1"],env=e).returncode
    shutil.rmtree(sd,ignore_errors=True)
    l1f=[f for f in os.listdir(od) if f.endswith(".l1.bin.zst")]
    msg=f"{key} orderbook-часов {ok}/24 сидов {len(seeds)}/{len(sj)} сбой {bad} rc {rc} l1-файлов {len(l1f)}"
    if bad or rc or len(l1f)!=ok: print("НЕ СОШЛОСЬ",msg,flush=True); continue
    open(od+"/.ok","w").write("carry=1 seeds=%d hours=%d\n"%(len(seeds),len(l1f))); print("ok",msg,flush=True)
