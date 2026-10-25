#!/usr/bin/env python3
"""TK-402 шаг 3b: max-T (Westfall–Young, одношаговый) по всем клеткам (ep>=30, D>=100) допущенных монет; общие 173 часа, B повторов с возвращением.
Выход: <out>.csv — клетки с t>=3,5 и p_adj; <out>.json — сводка (число клеток, квантиль max-T*, лидеры). Только numpy."""
import sys, glob, os, json, numpy as np
CIRCLE = {'full': 2 * (5.0 + 5.5), 'disc35': 2 * (5.0 * 0.65 + 5.5)}
AN = ['A0', 'med5m', 'med30m', 'med1h', 'med4h', 'med24h', 'ema5m', 'ema1h', 'ema24h', 'prevday']
H0 = 1792022400000 // 3600000; NH = 173
def load(d, cover, thr):
    adm = set()
    for l in open(cover).read().splitlines()[1:]:
        p = l.split('\t')
        if int(p[1]) >= thr: adm.add(p[0])
    meta, X = [], []; allmeta, allX = [], []
    for f in sorted(glob.glob(d + '/*.npz')):
        coin = os.path.basename(f)[:-4]; z = np.load(f); grid = [g.split(',') for g in z['grid']]
        circ = np.array([CIRCLE[g[0]] for g in grid]); D = np.array([int(g[2]) for g in grid]); nsegs = len(z['segs'])
        for a in AN:
            net = np.zeros((len(grid), 192)); ep = np.zeros_like(net); cnt = np.zeros_like(net)
            for s in range(nsegs):
                if f's{s}_{a}_sg' not in z.files: continue
                o = int(z[f's{s}_t0'][0] // 3600000) - H0; sg = z[f's{s}_{a}_sg']; n = sg.shape[1]; sl = slice(o, o + n)
                c = z[f's{s}_{a}_cnt']; net[:, sl] += sg - c * circ[:, None] + z[f's{s}_{a}_fg']; ep[:, sl] += z[f's{s}_{a}_ep']; cnt[:, sl] += c
            E = ep.sum(1); N = cnt.sum(1)
            for i in np.nonzero((E >= 30) & (D >= 100))[0]:
                row = (coin, a, ','.join(grid[i]), int(E[i]), int(N[i]))
                x = net[i, :NH].astype(np.float32)
                (meta if coin in adm else allmeta).append(row); (X if coin in adm else allX).append(x)
    return adm, meta, np.array(X), allmeta, np.array(allX)
def maxt(X, B, seed, chunk=4000, wchunk=2500):
    n = X.shape[1]; mu = X.mean(1); sd = X.std(1, ddof=1); ok = sd > 0
    tobs = np.where(ok, mu / np.where(ok, sd, 1) * np.sqrt(n), 0)
    Xc = X - mu[:, None]; X2 = Xc * Xc; sc = X2.mean(1)
    rng = np.random.default_rng(seed); mx = np.full(B, -np.inf, dtype=np.float32)
    for b0 in range(0, B, wchunk):
        b1 = min(B, b0 + wchunk); W = np.zeros((n, b1 - b0), dtype=np.float32)
        idx = rng.integers(0, n, size=(n, b1 - b0))
        for j in range(b1 - b0): W[:, j] = np.bincount(idx[:, j], minlength=n)
        W /= n
        for c0 in range(0, X.shape[0], chunk):
            m = Xc[c0:c0 + chunk] @ W; v = (X2[c0:c0 + chunk] @ W) - m * m
            v = v * n / (n - 1); good = v > 1e-3 * sc[c0:c0 + chunk, None]  # относительный порог: иначе шум float32 при нулевой дисперсии
            t = np.where(good, m / np.sqrt(np.where(good, v, 1) / n), 0)
            mx[b0:b1] = np.maximum(mx[b0:b1], t.max(0))
    return tobs, mx, ok
def main(d, cover, out, B=10000):
    adm, meta, X, allmeta, allX = load(d, cover, 165)
    print('допущено монет', len(adm), 'клеток', len(meta), 'не допущенных клеток', len(allmeta), flush=True)
    tobs, mx, ok = maxt(X, B, 402)
    pad = np.array([(mx >= t).mean() for t in tobs])
    q95 = float(np.quantile(mx, 0.95)); days = X[:, :168].reshape(len(X), 7, 24).sum(2)
    S = X.sum(1); order = np.argsort(-tobs)
    with open(out + '.csv', 'w') as o:
        o.write('coin,anchor,fee,k,D,T,ep,trades,net_sum_bp,net_per_trade_bp,days_pos_of7,t_hourly173,p_maxT\n')
        for i in order[:2000]:
            m = meta[i]; o.write(','.join([m[0], m[1], m[2], str(m[3]), str(m[4]), '%.2f' % S[i], '%.2f' % (S[i] / m[4] if m[4] else 0), str(int((days[i] > 0).sum())), '%.3f' % tobs[i], '%.4f' % pad[i]]) + '\n')
    good = [i for i in range(len(X)) if pad[i] < 0.05 and S[i] > 0 and (days[i] > 0).sum() >= 5]
    res = dict(admitted_coins=len(adm), cells=len(meta), B=B, q95_maxT=q95, q50_maxT=float(np.quantile(mx, .5)), top_t=float(tobs.max()), n_t_gt_q95=int((tobs > q95).sum()),
               n_good=len(good), cells_nonadm=len(allmeta), top5=[(meta[i][0], meta[i][1], meta[i][2], float(tobs[i]), float(pad[i])) for i in order[:5]])
    json.dump(res, open(out + '.json', 'w'), ensure_ascii=False, indent=1); print(res, flush=True)
if __name__ == '__main__': main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 10000)
