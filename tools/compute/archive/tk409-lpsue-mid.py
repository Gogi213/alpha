# TK-409: середины SUE (v2 BSC, WBNB за SUE -> USD по середине Gate BNB gev2) и LAPTOP (v3 Base, token0=USDC, цена = 1e12/mid_raw). Выход mid-<COIN>.csv.gz: ts,block,logidx,цена
import sys, csv, gzip, glob, numpy as np
B = '/data/tk0393/tk409/LS'; GEV = '/data/tk0393/tk401/gev2all'
a = np.concatenate([np.load(f) for f in sorted(glob.glob(f'{GEV}/BNB/??????????.npy'))])
a = a[np.argsort(a[:, 0], kind='stable')]; T = a[:, 0]; M = (a[:, 1] + a[:, 2]) / 2
def bnb(ts):  # последняя запись Gate BNB не позже ts (мс)
    i = np.searchsorted(T, ts * 1000, side='right') - 1
    return M[i] if i >= 0 else None
for coin, f, kinds in (('SUE', f'{B}/raw/bsc__0xffca411cfbcd3194503effa80fd394b55387915c.csv', 'v2'), ('LAPTOP', f'{B}/raw/base__0x99cf3e8bfb02c300312c53aac5d0b082e3d5975c.csv', 'v3')):
    rows = [r for r in csv.DictReader(open(f)) if r['kind'] == kinds]; n = 0
    with gzip.open(f'{B}/mid-{coin}.csv.gz', 'wt') as o:
        for r in rows:
            p = float(r['mid_raw'])
            if coin == 'SUE':
                b = bnb(int(r['ts']))
                if b is None: continue
                p *= b
            else:
                p = 1e12 / p
            o.write(f"{r['ts']},{r['block']},{r['logidx']},{float(p)!r}\n"); n += 1
    print(coin, 'n', n, 'first', rows[0]['ts'], 'last', rows[-1]['ts'], 'p0', p)
