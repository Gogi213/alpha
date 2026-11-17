import glob,re,pickle,json,collections
out=collections.defaultdict(lambda: collections.defaultdict(dict))
for f in sorted(glob.glob("/data/arb-hold/b3/gaps/*.pkl")):
    m=re.match(r".*/(\w+)-(\d{4}-\d\d-\d\d)-(\w+?)(\d\d)-(\d+)\.pkl",f)
    ex,run=m.group(1),m.group(5)
    for s,(n,first,last,gp,gn) in pickle.load(open(f,"rb")).items():
        o=out[ex][run].get(s)
        if o is None: out[ex][run][s]=[n,first,last]
        else: out[ex][run][s]=[o[0]+n,min(o[1],first),max(o[2],last)]
json.dump(out,open("/data/tk584/runsym.json","w"))
print({ex:{r:len(v) for r,v in d.items()} for ex,d in out.items() if ex=="gate"})
