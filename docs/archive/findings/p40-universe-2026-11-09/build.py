import json,re,collections,csv
S=json.load(open('p40u-symbols.json'))
B=json.load(open('bybit_inst.json'))['result']['list']
G=json.load(open('gate_c.json'))
arch=set(open('bybit_archive_syms.txt').read().split())
MULT=re.compile(r'^(1000000|10000000|100000|10000|1000|1M|k)(?=[A-Z])')
def strip_mult(b):
    m=MULT.match(b)
    if m and len(b)>len(m.group(0))+1:
        return b[m.end():]
    return b
ALIAS={'XAU':'GOLD','XAUT':'XAUT','XAG':'SILVER','USOIL':'WTI','CL':'WTI','BRENT':'BRENTOIL','BZ':'BRENTOIL','XBT':'BTC','GOOGL':'GOOGL','GOOG':'GOOGL','NATGAS':'GAS','UKOIL':'BRENTOIL','XCU':'COPPER','XPT':'PLATINUM','XPD':'PALLADIUM'}
SUF=[('-USDT-SWAP',''),('-USDC-SWAP','|USDC'),('-USD_UM-SWAP','|SKIP'),('-USD-SWAP','|SKIP'),('_USDT',''),('USDT',''),('USDC','|USDC'),('PERP',''),('USD1','|SKIP'),('USD','|SKIP')]
def norm(ex,s):
    if ex=='hyperliquid_futures':
        dex,_,b=s.rpartition(':'); b=b or s
        return ALIAS.get(strip_mult(b.upper()),strip_mult(b.upper())) if not b.startswith('k') else strip_mult(b), (dex or 'native')
    b=s; tag=''
    if re.search(r'_\d{6}$',b) or re.search(r'USD[HMUZ]\d\d$',b): return None,'dated'
    for suf,t in SUF:
        if b.endswith(suf): b=b[:-len(suf)]; tag=t; break
    else:
        if ex=='gate_futures': pass
        else: return None,'noquote'
    if tag in('|SKIP','|USDC'): return None,'nonUSDT'
    b=b.upper()
    if b.endswith('STOCK') and len(b)>6: b=b[:-5]
    b=strip_mult(b)
    return ALIAS.get(b,b),''
# Bybit
byb={}
cls={}  # asset -> class
for x in B:
    if x['contractType']!='LinearPerpetual' or not x['symbol'].endswith('USDT'): continue
    t=x.get('underlyingTicker') or ''
    b,_=norm('bybit_futures',x['symbol'])
    a=ALIAS.get(t,t) if t else b
    byb[a]=x['symbol']
    st=x.get('symbolType')
    if st in('stock','ETF','commodity','forex'): cls[a]={'stock':'акции','ETF':'ETF','commodity':'сырьё/металлы','forex':'FX'}[st]
gate={}
for c in G:
    n=c['name']; b=n[:-5] if n.endswith('_USDT') else n
    b=strip_mult(b.upper()); b=ALIAS.get(b,b); gate[b]=n
EX={'binance_futures':{},'okx_futures':{},'bitget_futures':{},'aster_futures':{},'hyperliquid_futures':{}}
hl_dex={}
for ex in EX:
    for s in S[ex]['orderbook']:
        a,tag=norm(ex,s)
        if a is None: continue
        EX[ex][a]=s
        if ex=='hyperliquid_futures' and tag not in('native','HYNA'): hl_dex[a]=tag
EX['bybit_futures']=byb; EX['gate_futures']=gate
# classify: known TradFi = bybit-classified, HL dex-listed (non-native) , gate/binance list vs bybit cls
hl_native={a for a,s in EX['hyperliquid_futures'].items() if ':' not in s}
for a in hl_native: hl_dex.pop(a,None)
TF=set(cls)|set(hl_dex)
METALS={'GOLD','SILVER','PLATINUM','PALLADIUM','COPPER','XAUT','PAXG'}
ENERGY={'WTI','BRENTOIL','GAS','OIL'}
def klass(a):
    if a in cls: return cls[a]
    if a in METALS: return 'сырьё/металлы'
    if a in ENERGY: return 'сырьё/металлы'
    if a in hl_dex: return 'TradFi (прочее/акции)'
    return 'крипта'
assets=sorted(set().union(*[set(v) for v in EX.values()]))
rows=[]
for a in assets:
    ex=[e for e in EX if a in EX[e]]
    rows.append((a,klass(a),len(ex),ex))
multi=[r for r in rows if r[2]>=2]
print('assets',len(assets),'on>=2',len(multi))
T=collections.Counter((r[1]) for r in multi); print(T)
# per exchange x class: contracts on >=2 assets
tab=collections.defaultdict(collections.Counter)
for a,k,n,ex in multi:
    for e in ex: tab[e][k]+=1
for e in EX: print(e,len(EX[e]),dict(tab[e]))
# Bybit archive coverage
print('bybit perps in archive:',sum(1 for a,s in byb.items() if s in arch),'of',len(byb))
json.dump({'rows':rows,'tab':{e:dict(v) for e,v in tab.items()}},open('universe.json','w'),ensure_ascii=False)
with open('universe.csv','w',newline='',encoding='utf8') as f:
    w=csv.writer(f); w.writerow(['asset','class','n_exch']+list(EX))
    for a,k,n,ex in multi: w.writerow([a,k,n]+[EX[e].get(a,'') for e in EX])
# pairs count per class: assets x C(n,2)
import math
pc=collections.Counter()
for a,k,n,ex in multi: pc[k]+=math.comb(n,2)
print('pairs',pc, sum(pc.values()))
print([r[0] for r in multi if r[1]!='крипта'][:80])
print([ (r[0],r[2]) for r in multi if r[1]=='крипта'][:15])
