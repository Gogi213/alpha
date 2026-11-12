import json,sys,urllib.request,time
pools=json.load(open("/data/tk0374/pools32.json"))
URL={"ethereum":["https://eth.drpc.org","https://ethereum-rpc.publicnode.com"],"base":["https://base.drpc.org","https://mainnet.base.org"]}
def call(ch,addr,data):
    for i in range(8):
        for u in URL[ch]:
            try:
                b=json.dumps({"jsonrpc":"2.0","id":1,"method":"eth_call","params":[{"to":addr,"data":data},"latest"]}).encode()
                r=json.load(urllib.request.urlopen(urllib.request.Request(u,b,{"content-type":"application/json","user-agent":"Mozilla/5.0"}),timeout=30))
                return r
            except Exception as e: time.sleep(2*(i+1))
    return None
out={}
for k,v in pools.items():
    r=call(v["chain"],v["pool"],"0xddca3f43")
    res=(r or {}).get("result")
    if res and res!="0x" and len(res)>=66:
        out[k]={"fee_bp":int(res,16)/100.0,"src":"v3 fee() on-chain"}
    else:
        out[k]={"fee_bp":30.0,"src":"v2: нет fee(), 0,30 pct по умолчанию (%s)"%(v.get("labels") or v.get("dex")),"raw":str(r)[:80]}
json.dump(out,open("/data/tk0374/pool-fees.json","w"),indent=1,ensure_ascii=False)
from collections import Counter; print(Counter(o["fee_bp"] for o in out.values()))
