"""Судья 27.09: устойчивость правила П-07 без одних суток — min и max доли > 5 сут по исключениям (verdict_kpi берёт
только max; «не проходит» требует min > 0,10)."""
import importlib.util, sys
spec = importlib.util.spec_from_file_location("kn", "tools/compute/kpi-newhigh.py")
kn = importlib.util.module_from_spec(spec); spec.loader.exec_module(kn)
S = kn.load("data/kpi")
DAY = 86_400_000
for nm in sys.argv[1:]:
    cl = S[nm][1]["augsep"]
    base = kn.rolling_kpi(cl, h_days=5)
    days = sorted({(t - kn.ms("2026-08-01")) // DAY for t, _ in cl})
    fa, fs = [], []
    for d in days:
        r = kn.rolling_kpi([(t, p) for t, p in cl if (t - kn.ms("2026-08-01")) // DAY != d], h_days=5)
        fa.append(r["aug"]["frac_gt_h"]); fs.append(r["sep"]["frac_gt_h"])
    print(f"{nm}: точка {base['aug']['frac_gt_h']:.2f}/{base['sep']['frac_gt_h']:.2f}; без одних суток авг min {min(fa):.2f} max {max(fa):.2f}, сен min {min(fs):.2f} max {max(fs):.2f}")
