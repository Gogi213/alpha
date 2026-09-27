"""Судья 27.09: П-05 «равный риск» перекрёстно (протокол §3а: k по месяцу подбора → другой месяц) против отчётов, где
k взят из того же месяца. k_dd = просадка главного / просадка клетки; k_sd = σ суток главного / σ суток клетки, ≤ 3.
Считает: сколько клеток с ≥ 30 сделками в обоих месяцах имеют Δ > 0 в обоих месяцах (точка) и нижнюю границу > 0."""
import glob, json, math, random, statistics as st

MONTHS = ("август", "сентябрь")


def boot(x, rng, reps=5000):
    n = len(x); L = max(1, math.ceil(n ** (1 / 3))); out = []
    for _ in range(reps):
        s = []
        while len(s) < n:
            i = rng.randrange(n); s += [x[(i + j) % n] for j in range(L)]
        out.append(sum(s[:n]))
    out.sort(); return out[int(.025 * reps)], out[int(.975 * reps)]


G = {}
for f in glob.glob("data/p05/protection-p05-[abc]*.json"):
    for g in json.load(open(f, encoding="utf-8"))["grid"]:
        if g["max_pos"] == 0 and g["kill"] == 0 and g["exclude"] == "нет" and g["day_stop"] == 0 and g.get("streak_stop", 0) == 0:
            G.setdefault((g["variant"], g["epoch"]), g)
rng = random.Random(7)
cells = sorted({v for v, _ in G if v != "главный"})
res = {"same_dd": [], "cross_dd": [], "cross_sd": []}
for v in cells:
    if any((v, m) not in G or G[(v, m)]["n"] < 30 for m in MONTHS):
        continue
    days = {m: sorted(set(G[(v, m)]["daily"]) | set(G[("главный", m)]["daily"])) for m in MONTHS}
    ser = {m: ([G[(v, m)]["daily"].get(d, 0) for d in days[m]], [G[("главный", m)]["daily"].get(d, 0) for d in days[m]]) for m in MONTHS}
    kdd = {m: G[("главный", m)]["dd_pct"] / G[(v, m)]["dd_pct"] for m in MONTHS}
    ksd = {m: min(3.0, st.pstdev(ser[m][1]) / st.pstdev(ser[m][0])) for m in MONTHS}
    for tag, k in (("same_dd", {m: kdd[m] for m in MONTHS}),
                   ("cross_dd", {"август": kdd["сентябрь"], "сентябрь": kdd["август"]}),
                   ("cross_sd", {"август": ksd["сентябрь"], "сентябрь": ksd["август"]})):
        row = []
        for m in MONTHS:
            d = [a * k[m] - b for a, b in zip(*ser[m])]
            row.append((sum(d), *boot(d, rng)))
        res[tag].append((v, row))
for tag, rows in res.items():
    both = [v for v, r in rows if all(x[0] > 0 for x in r)]
    sig = [v for v, r in rows if all(x[1] > 0 for x in r)]
    anysig = [v for v, r in rows if any(x[1] > 0 for x in r)]
    print(f"{tag}: клеток {len(rows)}; Δ>0 в обоих {len(both)}; нижняя>0 в обоих {len(sig)} {sig}; нижняя>0 хотя бы в одном {len(anysig)}")
for v, r in res["same_dd"]:
    if any(x[1] > 0 for x in r):
        c = dict(res["cross_dd"])[v]
        print(f"  {v}: тот же месяц " + "; ".join(f"{x[0]:+.0f} [{x[1]:+.0f}; {x[2]:+.0f}]" for x in r)
              + " | перекрёстно " + "; ".join(f"{x[0]:+.0f} [{x[1]:+.0f}; {x[2]:+.0f}]" for x in c))
