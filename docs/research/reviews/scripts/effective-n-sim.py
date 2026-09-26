# Судья 26.09: как часто effective_n_from_autocorr даёт «не определено» (правило aed6288) и что даёт потолок n, AR(1), 4000 повторов; запуск из корня репозитория
import sys, importlib.util, numpy as np
spec = importlib.util.spec_from_file_location("en", ".claude/skills/alpha-research/scripts/effective_n.py")
en = importlib.util.module_from_spec(spec); spec.loader.exec_module(en)
rng = np.random.default_rng(7)
def ar1(n, phi):
    x = np.zeros(n); e = rng.normal(size=n)
    x[0] = e[0]
    for t in range(1, n): x[t] = phi * x[t-1] + e[t]
    return x
for phi in (0.0, 0.2, 0.5, -0.3):
    true_f = (1 + phi) / (1 - phi)
    for n in (23, 40, 100, 290):
        none = 0; vals = []
        for _ in range(4000):
            v = en.effective_n_from_autocorr(ar1(n, phi))
            if v is None: none += 1
            else: vals.append(v)
        vals = np.array(vals)
        ge30 = ((vals >= 30).sum()) / 4000
        print(f"phi {phi:+.1f} n {n:3d} (истинное эфф.N {n/true_f:6.1f}): не определено {none/4000:5.1%}  медиана {np.median(vals) if len(vals) else float('nan'):6.1f}  доля >=30 {ge30:5.1%}")
print("---- потолок: factor<=0 -> n; иначе min(n, n/factor)")
def capped(r):
    r = np.asarray(r, float); n = len(r)
    v = en.effective_n_from_autocorr(r)
    return float(n) if v is None else min(float(n), v)
for phi in (0.0, 0.2, 0.5, -0.3):
    true_f = (1 + phi) / (1 - phi)
    for n in (23, 40, 100, 290):
        vals = np.array([capped(ar1(n, phi)) for _ in range(4000)])
        print(f"phi {phi:+.1f} n {n:3d} (истинное {n/true_f:6.1f}): медиана {np.median(vals):6.1f}  доля >=30 {(vals>=30).mean():5.1%}  доля ==n {(vals==n).mean():5.1%}")
