#!/usr/bin/env python3
"""TK-402 шаг 3a: свод npz step2 по 459 монетам -> клетки (монета, опора, клетка): эпизоды, сделки, net (бп), дни >0, t по часам. Только D>=100 (D=0 справочно), эфф. N=ep>=30."""
import sys, glob, os, numpy as np
CIRCLE = {'full': 2 * (5.0 + 5.5), 'disc35': 2 * (5.0 * 0.65 + 5.5)}
AN = ['A0', 'med5m', 'med30m', 'med1h', 'med4h', 'med24h', 'ema5m', 'ema1h', 'ema24h', 'prevday']
H0 = 1792022400000 // 3600000; NHG = 192
def main(d, out, outh):
    rows = []; H = {}
    for f in sorted(glob.glob(d + '/*.npz')):
        coin = os.path.basename(f)[:-4]; z = np.load(f); grid = [g.split(',') for g in z['grid']]
        circ = np.array([CIRCLE[g[0]] for g in grid]); D = np.array([int(g[2]) for g in grid])
        nsegs = len(z['segs'])
        for a in AN:
            net = np.zeros((len(grid), NHG)); ep = np.zeros_like(net); cnt = np.zeros_like(net)
            for s in range(nsegs):
                if f's{s}_{a}_sg' not in z.files: continue
                o = int(z[f's{s}_t0'][0] // 3600000) - H0; sg = z[f's{s}_{a}_sg']; n = sg.shape[1]; sl = slice(o, o + n)
                c = z[f's{s}_{a}_cnt']; net[:, sl] += sg - c * circ[:, None] + z[f's{s}_{a}_fg']; ep[:, sl] += z[f's{s}_{a}_ep']; cnt[:, sl] += c
            E = ep.sum(1); N = cnt.sum(1); S = net.sum(1)
            dn = net[:, :168].reshape(len(grid), 7, 24).sum(2)
            sd = net.std(1, ddof=1); t = np.where(sd > 0, net.mean(1) / (sd / np.sqrt(NHG)), 0)
            for i in np.nonzero((E >= 30) & (D >= 100))[0]:
                rows.append((coin, a, ','.join(grid[i]), int(E[i]), int(N[i]), S[i], S[i] / N[i] if N[i] else 0, int((dn[i] > 0).sum()), t[i]))
                H[(coin, a, i)] = net[i]
    with open(out, 'w') as o:
        o.write('coin,anchor,fee,k,D,T,ep,trades,net_sum_bp,net_per_trade_bp,days_pos_of7,t_hourly\n')
        for r in rows: o.write(','.join([r[0], r[1], r[2], str(r[3]), str(r[4]), '%.2f' % r[5], '%.3f' % r[6], str(r[7]), '%.2f' % r[8]]) + '\n')
    print('клеток с ep>=30, D>=100:', len(rows), 'из них net>0:', sum(r[5] > 0 for r in rows), 'net>0 и дней>=5:', sum(r[5] > 0 and r[7] >= 5 for r in rows), flush=True)
if __name__ == '__main__': main(sys.argv[1], sys.argv[2], None)
