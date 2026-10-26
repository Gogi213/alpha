#!/usr/bin/env python3
"""TK-410 rest3: сводка контроля-2 (curve3) и В2/В3/В5 (conv) по out6/out-sui. Вход: каталоги; выход: sum3-ctrl2.csv, sum3-b235.csv (cwd)."""
import sys, glob, os, numpy as np, pandas as pd
dirs = sys.argv[1:]; ctrl = []; b = []
for d in dirs:
    for f in sorted(glob.glob(f'{d}/*-curve3.csv')):
        coin = os.path.basename(f).split('-')[0]; r = pd.read_csv(f)
        q = r[(r.n >= 30) & (r.n_days >= 3)]; q3 = q[q.exc3.notna()]
        a = q3[q3.ci_lo > 2.6]; nc = len(q3)
        p = a[(a.ci3_lo > 2.6) & (a.perm_p * max(nc, 1) < 0.05)]
        ctrl.append(dict(coin=coin, rows=len(r), cells=len(q3), ci_lo_gt26=len(a), pass_ctrl2=int((a.ci3_lo > 2.6).sum()), pass_ctrl2_perm_bonf=len(p),
                         max_exc3=float(q3.exc3.max()) if nc else np.nan, max_ci3_lo=float(q3.ci3_lo.max()) if nc else np.nan, min_perm_p=float(q3.perm_p.min()) if nc else np.nan))
    for f in sorted(glob.glob(f'{d}/*-conv.csv')):
        coin = os.path.basename(f).split('-')[0]; c = pd.read_csv(f)
        if not len(c): continue
        t = c.tconv_s.dropna()
        b.append(dict(coin=coin, episodes=len(c), conv=len(t), conv_share=len(t) / len(c), t_p50=t.quantile(.5) if len(t) else np.nan, t_p90=t.quantile(.9) if len(t) else np.nan,
                      share_gate_med=c.share_gate.median(), gate_first=(c['first'] == 'gate').mean(), pool_first=(c['first'] == 'pool').mean(), none=(c['first'] == 'none').mean()))
pd.DataFrame(ctrl).to_csv('sum3-ctrl2.csv', index=False); pd.DataFrame(b).to_csv('sum3-b235.csv', index=False)
print(pd.DataFrame(ctrl).to_string()); print(pd.DataFrame(b).round(3).to_string())
