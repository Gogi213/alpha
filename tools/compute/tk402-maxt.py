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
def maxt(X, B, seed, chunk=4000, wchunk=1000, stepdown=True):
    """Westfall–Young max-T. t* = (m* - mu)/se_obs (SE исходной выборки, float64); пошаговый: p_j по max над клетками с рангом >= j."""
    X = X.astype(np.float64); n = X.shape[1]; mu = X.mean(1); sd = X.std(1, ddof=1); ok = sd > 0
    se = np.where(ok, sd, 1.0) / np.sqrt(n)
    tobs = np.where(ok, mu / se, 0.0); order = np.argsort(tobs)  # по возрастанию t: снизу вверх идёт бегущий максимум
    Xc = (X - mu[:, None])[order]; se_o = se[order]; ok_o = ok[order]; t_o = tobs[order]
    rng = np.random.default_rng(seed); mx = np.full(B, -np.inf); cnt = np.zeros(len(t_o))
    for b0 in range(0, B, wchunk):
        b1 = min(B, b0 + wchunk); W = np.zeros((n, b1 - b0))
        idx = rng.integers(0, n, size=(n, b1 - b0))
        for j in range(b1 - b0): W[:, j] = np.bincount(idx[:, j], minlength=n)
        W /= n; run = np.full(b1 - b0, -np.inf)
        for c0 in range(0, len(t_o), chunk):
            c1 = min(len(t_o), c0 + chunk)
            T = np.where(ok_o[c0:c1, None], (Xc[c0:c1] @ W) / se_o[c0:c1, None], -np.inf)
            if stepdown:
                T = np.maximum.accumulate(T, axis=0); T = np.maximum(T, run[None, :]); run = T[-1]
                cnt[c0:c1] += (T >= t_o[c0:c1, None]).sum(1)
            else:
                run = np.maximum(run, T.max(0))
        mx[b0:b1] = run
    p_o = cnt / B if stepdown else np.array([(mx >= t).mean() for t in t_o])
    p_o = np.maximum.accumulate(p_o[::-1])[::-1]  # монотонность: у меньшего t p не меньше, чем у большего
    p = np.empty_like(p_o); p[order] = p_o
    return tobs, mx, ok, p
def sanity(X, B, seed=7, n_cells=2000):
    """Истинный нуль: знаки часов случайны (общие для всех клеток); q95 одношагового max-T против z Бонферрони (одностор.)."""
    from statistics import NormalDist
    rng = np.random.default_rng(seed); Xs = X[rng.choice(len(X), n_cells, replace=False)].astype(np.float64)
    Xs = Xs * rng.choice([-1.0, 1.0], size=(1, Xs.shape[1]))
    _, mx, _, _ = maxt(Xs, B, seed, stepdown=False)
    q = float(np.quantile(mx, .95)); z = NormalDist().inv_cdf(1 - .05 / n_cells)
    return dict(n_cells=n_cells, q50=float(np.quantile(mx, .5)), q95=q, z_bonf=z, ratio=q / z, ok=bool(abs(q / z - 1) <= .2))
def main(d, cover, out, B=10000):
    adm, meta, X, allmeta, allX = load(d, cover, 165)
    print('допущено монет', len(adm), 'клеток', len(meta), 'не допущенных клеток', len(allmeta), flush=True)
    san = sanity(X, 1000); print('санити', san, flush=True)
    if not san['ok']: sys.exit('санити max-T не прошла')
    tobs, mx, ok, pad = maxt(X, B, 402)
    q95 = float(np.quantile(mx, 0.95)); days = X[:, :168].reshape(len(X), 7, 24).sum(2)
    S = X.sum(1); order = np.argsort(-tobs)
    with open(out + '.csv', 'w') as o:
        o.write('coin,anchor,fee,k,D,T,ep,trades,net_sum_bp,net_per_trade_bp,days_pos_of7,t_hourly173,p_stepdown\n')
        for i in order[:2000]:
            m = meta[i]; o.write(','.join([m[0], m[1], m[2], str(m[3]), str(m[4]), '%.2f' % S[i], '%.2f' % (S[i] / m[4] if m[4] else 0), str(int((days[i] > 0).sum())), '%.3f' % tobs[i], '%.4f' % pad[i]]) + '\n')
    good = [i for i in range(len(X)) if pad[i] < 0.05 and S[i] > 0 and (days[i] > 0).sum() >= 5]
    res = dict(admitted_coins=len(adm), cells=len(meta), B=B, sanity=san, q95_maxT=q95, q50_maxT=float(np.quantile(mx, .5)), top_t=float(tobs.max()), n_t_gt_q95=int((tobs > q95).sum()),
               n_good=len(good), cells_nonadm=len(allmeta), top5=[(meta[i][0], meta[i][1], meta[i][2], float(tobs[i]), float(pad[i])) for i in order[:5]])
    json.dump(res, open(out + '.json', 'w'), ensure_ascii=False, indent=1); print(res, flush=True)
if __name__ == '__main__': main(sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 10000)
