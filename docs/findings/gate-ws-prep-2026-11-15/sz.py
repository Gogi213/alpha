import json
tot={"book":[0,0],"trade":[0,0],"mark":[0,0]}
for k in tot:
    for l in open(f"/data/gate-probe/frames/w1-{k}.jsonl"):
        j=json.loads(l)
        if "f" in j and '"update"' in j["f"][:140]: tot[k][0]+=1; tot[k][1]+=len(j["f"])
print({k:(round(v[0]/185), round(v[1]/185/1024,1), round(v[1]/max(v[0],1))) for k,v in tot.items()}, "frames/s, KiB/s, avg bytes")
