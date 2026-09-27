"""Судья 27.09: пересчёт §6 retries (a01d7f6) — «только N-й» с «без номера» и без них; возраст стены по номеру."""
import csv, importlib.util, statistics as st
spec = importlib.util.spec_from_file_location("kn", "tools/compute/kpi-newhigh.py")
kn = importlib.util.module_from_spec(spec); spec.loader.exec_module(kn)
T = list(csv.DictReader(open("data/t32/main-trades.csv", encoding="utf-8")))
L = {(r["sym"], r["t0_ms"]): r for r in csv.DictReader(open("data/t32/retries-link.csv", encoding="utf-8"))}
def num(t):
    r = L.get((t["sym"], t["t0_ms"]))
    return int(r["approach_number_at_entry"]) if r and r["status"] == "linked" else None
def kpi(xs):
    r = kn.rolling_kpi([(int(t["t1_ms"]), float(t["pnl_usd"])) for t in xs], h_days=5)
    return f"{r['aug']['frac_gt_h']:.2f} / {r['sep']['frac_gt_h']:.2f}"
print("база", len(T), kpi(T))
for n in (1, 2, 3):
    a = [t for t in T if num(t) in (n, None)]; b = [t for t in T if num(t) == n]
    print(f"только {n}: с «без номера» n={len(a)} {kpi(a)} | без них n={len(b)} {kpi(b)}")
for n in range(1, 6):
    ages = [(int(t["t0_ms"]) - int(L[(t["sym"], t["t0_ms"])]["birth_ms"])) / 60000 for t in T if num(t) == n]
    if ages: print(f"номер {n}: n={len(ages)} медиана возраста {st.median(ages):.0f} мин, доля ≥ 2 ч {sum(a >= 120 for a in ages)/len(ages):.2f}")
