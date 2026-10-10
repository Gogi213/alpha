"""Судья 27.09: R² накопленной кривой по суткам месяца против перестановок тех же суток (насколько путь ровнее
случайного порядка тех же дневных результатов)."""
import importlib.util, random, sys
spec = importlib.util.spec_from_file_location("kn", "tools/compute/archive/kpi-newhigh.py")
kn = importlib.util.module_from_spec(spec); spec.loader.exec_module(kn)
S = kn.load("data/kpi"); D = 86_400_000
def r2(y):
    n = len(y); x = list(range(n)); mx = sum(x) / n; my = sum(y) / n
    sxy = sum((a - mx) * (b - my) for a, b in zip(x, y)); sxx = sum((a - mx) ** 2 for a in x); syy = sum((b - my) ** 2 for b in y)
    return sxy * sxy / (sxx * syy) if syy else 0.0
def cum(d):
    c, out = 0.0, []
    for v in d: c += v; out.append(c)
    return out
rng = random.Random(7)
for nm in sys.argv[1:]:
    cl = S[nm][1]["augsep"]
    for m, (s, n) in {"авг": (kn.ms("2026-08-01"), 31), "сен": (kn.ms("2026-09-01"), 23)}.items():
        d = [0.0] * n
        for t, p in cl:
            k = (t - s) // D
            if 0 <= k < n: d[k] += p
        r = r2(cum(d)); sh = []
        for _ in range(2000):
            e = d[:]; rng.shuffle(e); sh.append(r2(cum(e)))
        sh.sort()
        print(f"{nm} {m}: R² {r:.2f}; перестановки медиана {sh[1000]:.2f}, 90 % {sh[1800]:.2f}; ровнее перестановок в {sum(x < r for x in sh) / 20:.0f} %")
