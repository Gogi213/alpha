"""TK-461 Q-3: свод по варианту из q3-years.csv (tk461_q3.py): net worst/best по годам, критерий Q-3.
python tk461_q3_sum.py <q3-years.csv> <out.csv>"""
import sys, pandas as pd
K = ['tf', 'ev', 'dir', 'side', 'stop', 'take', 'par', 'be_r', 'ot', 'otp']
y = pd.read_csv(sys.argv[1], dtype={k: str for k in K})
y = y[y.n > 0]
def f(g):
    return pd.Series({
        'годов': len(g), 'n': g.n.sum(),
        'worst_общ': (g.net_worst * g.n).sum() / g.n.sum(), 'best_общ': (g.net_best * g.n).sum() / g.n.sum(),
        'worst_мин_год': g.net_worst.min(), 'lo_worst_мин_год': g.lo_worst.min(),
        'все_годы_worst>0': int((g.net_worst > 0).all()), 'все_годы_lo_worst>0': int((g.lo_worst > 0).all()),
        'worst_по_годам': ' '.join(f"{a}:{b:.1f}" for a, b in zip(g.year, g.net_worst)),
        'best_по_годам': ' '.join(f"{a}:{b:.1f}" for a, b in zip(g.year, g.net_best))})
s = y.groupby(K).apply(f).reset_index()
s.to_csv(sys.argv[2], index=False, float_format='%.3f')
print(len(s), 'вариантов; worst_общ>0:', int((s.worst_общ > 0).sum()), '; best_общ>0:', int((s.best_общ > 0).sum()),
      '; все годы worst>0:', int(s['все_годы_worst>0'].sum()), '; все годы нижняя граница worst>0:', int(s['все_годы_lo_worst>0'].sum()))
print(s.sort_values('worst_мин_год', ascending=False).head(5)[K + ['n', 'worst_общ', 'worst_мин_год', 'lo_worst_мин_год']].to_string())
