#!/usr/bin/env python3
"""TK-394: свод замера с задержкой 1 с (поля lag_* от tk394-spread.py) -> csv + печать."""
import sys, glob, json, csv
import numpy as np
src, out = sys.argv[1], sys.argv[2]
rows = []
for f in glob.glob(f'{src}/*.json'): rows += json.load(open(f))
by = {}
for r in rows:
    if 'lag_n' in r: by.setdefault((r['pair'], r['coin']), []).append(r)
res = []
for (pair, coin), rs in sorted(by.items()):
    n = np.array([r['lag_n'] for r in rs], float); N = np.array([r['n_exec'] for r in rs], float)
    def w(k):
        v = np.array([r.get(k, np.nan) for r in rs], float); m = np.isfinite(v) & (n > 0)
        return float((v[m] * n[m]).sum() / n[m].sum()) if m.any() else np.nan
    res.append(dict(pair=pair, coin=coin, days=len(rs), entry_bp=rs[0]['entry_bp'], lag_n_day=float(n.mean()), share_sig=float(n.sum() / N.sum()),
                    mean_g=w('lag_mean_g'), med_g=w('lag_med_g'), share_g_gt0=w('lag_share_gt_0'), share_g_gt_entry=w('lag_share_gt_entry'), mean_net_entry=w('lag_mean_net'),
                    d_med=float(np.mean([r['d_med'] for r in rs])), d_sd=float(np.mean([r['d_sd'] for r in rs]))))
cols = list(res[0])
with open(out, 'w', newline='', encoding='utf-8') as f:
    wr = csv.DictWriter(f, cols, delimiter=';'); wr.writeheader()
    for o in res: wr.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in o.items()})
print('pair coins | med share_sig | med mean_g | med share_g>0 | med share_g>entry | med mean_net | coins mean_net>0 | coins mean_net>0 and |d_med|<entry')
for pair in ('FF', 'FS', 'SF', 'SS', 'GB', 'BB'):
    L = [o for o in res if o['pair'] == pair]
    if not L: continue
    g = lambda k: np.nanmedian([o[k] for o in L])
    pos = [o for o in L if o['mean_net_entry'] > 0]
    print(pair, len(L), f"{g('share_sig'):.4f} {g('mean_g'):.2f} {g('share_g_gt0'):.3f} {g('share_g_gt_entry'):.3f} {g('mean_net_entry'):.2f}", len(pos), sum(1 for o in pos if abs(o['d_med']) < o['entry_bp']))
