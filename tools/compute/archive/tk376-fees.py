#!/usr/bin/env python3
"""TK-376: fee() всех пулов эпизодов. usage: tk376-fees.py pools-all.json OUT.json. v3: fee() on-chain; нет fee() -> v2 по умолчанию (pancake 25, прочие 30 бп) — помечено src."""
import json,sys,urllib.request,time,concurrent.futures as cf
pools=json.load(open(sys.argv[1]))
URL={"ethereum":["https://eth.drpc.org","https://ethereum-rpc.publicnode.com"],"base":["https://base.drpc.org","https://mainnet.base.org"],"bsc":["https://bsc-rpc.publicnode.com","https://bsc.drpc.org"]}
def call(ch,addr,data):
    for i in range(5):
        for u in URL[ch]:
            try:
                b=json.dumps({"jsonrpc":"2.0","id":1,"method":"eth_call","params":[{"to":addr,"data":data},"latest"]}).encode()
                return json.load(urllib.request.urlopen(urllib.request.Request(u,b,{"content-type":"application/json","user-agent":"Mozilla/5.0"}),timeout=20))
            except Exception as e: time.sleep(1+i)
    return None
def one(p):
    ch,addr,dex=p
    r=call(ch,addr,"0xddca3f43"); res=(r or {}).get("result")
    if res and res!="0x" and len(res)>=66: return f"{ch}:{addr}",{"fee_bp":int(res,16)/100.0,"src":"v3 fee()","dex":dex}
    if r is None: return f"{ch}:{addr}",{"fee_bp":None,"src":"rpc fail","dex":dex}
    return f"{ch}:{addr}",{"fee_bp":25.0 if dex=="pancakeswap" else 30.0,"src":"v2 default","dex":dex}
with cf.ThreadPoolExecutor(12) as ex: out=dict(ex.map(one,pools))
json.dump(out,open(sys.argv[2],"w"),indent=1)
from collections import Counter; print(Counter((v["src"],v["fee_bp"]) for v in out.values()))
