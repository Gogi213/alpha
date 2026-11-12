#!/usr/bin/env python3
"""TK-394: свод замера расхождений (вход data/tk394/*.json от tk394-spread.py) -> csv + печать."""
import sys, glob, json, csv
import numpy as np
src, out = sys.argv[1], sys.argv[2]
rows = []
for f in glob.glob(f'{src}/*.json'):
    rows += json.load(open(f))
by = {}
for r in rows: by.setdefault((r['pair'], r['coin']), []).append(r)
def w(rs, k):  # средневзвешенное по n_exec (или n)
    ww = np.array([r.get('n_exec', r['n']) for r in rs], float)
    v = np.array([r.get(k, np.nan) for r in rs], float)
    m = np.isfinite(v)
    return float((v[m] * ww[m]).sum() / ww[m].sum()) if m.any() and ww[m].sum() > 0 else np.nan
cols = ['pair', 'coin', 'days', 'entry_bp', 'd_med', 'd_sd', 'd_abs_med', 'share_abs_gt_entry', 'e_med', 'share_gt_0', 'share_gt_entry', 'share_gt_round',
        'ep_per_day_entry', 'ep_per_day_round', 'ep_med_len_entry', 'ep_p90_len_entry', 'ep_med_len_round', 'n_exec_mean']
res = []
for (pair, coin), rs in sorted(by.items()):
    o = dict(pair=pair, coin=coin, days=len(rs), entry_bp=rs[0]['entry_bp'])
    for k in ('d_med', 'd_sd', 'd_abs_med', 'share_abs_gt_entry', 'e_med', 'share_gt_0', 'share_gt_entry', 'share_gt_round', 'ep_med_len_entry', 'ep_p90_len_entry', 'ep_med_len_round'):
        o[k] = w(rs, k)
    o['ep_per_day_entry'] = float(np.mean([r.get('ep_gt_entry', np.nan) for r in rs])) if any('ep_gt_entry' in r for r in rs) else np.nan
    o['ep_per_day_round'] = float(np.mean([r.get('ep_gt_round', np.nan) for r in rs])) if any('ep_gt_round' in r for r in rs) else np.nan
    o['n_exec_mean'] = float(np.mean([r.get('n_exec', 0) for r in rs]))
    res.append(o)
with open(out, 'w', newline='', encoding='utf-8') as f:
    wr = csv.DictWriter(f, cols, delimiter=';'); wr.writeheader()
    for o in res: wr.writerow({k: (round(v, 4) if isinstance(v, float) else v) for k, v in o.items()})
print('pair  coins  entry  med|d-med|  med share_abs>entry  med share_exec>0  >entry  >round  coins_with_>round_days  ep/day>entry(med) ep/day>round(med) medlen_entry')
for pair in ('FF', 'FS', 'SF', 'SS', 'GB', 'BB'):
    L = [o for o in res if o['pair'] == pair]
    if not L: continue
    g = lambda k: np.nanmedian([o[k] for o in L]) if any(np.isfinite(o[k]) for o in L) else float('nan')
    print(pair, len(L), L[0]['entry_bp'], f"{g('d_abs_med'):.2f}", f"{g('share_abs_gt_entry'):.5f}", f"{g('share_gt_0'):.4f}", f"{g('share_gt_entry'):.5f}", f"{g('share_gt_round'):.5f}",
          sum(1 for o in L if (o['share_gt_round'] or 0) > 0), f"{g('ep_per_day_entry'):.1f}", f"{g('ep_per_day_round'):.1f}", f"{g('ep_med_len_entry'):.1f}")
