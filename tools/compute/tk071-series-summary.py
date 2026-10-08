"""TK-071 п.3: сводка серии A=A на стенде d15 → stdout. Метки L<k>a/b (под нагрузкой, --iso 4), E<k>a/b (пустое окно)."""
import json, os

D, S, O = "/data/tk071/series", "/data/sched", "/data/tk051/stand-out"
ids = {}
for l in open(f"{D}/ids.txt"):
    p = l.split()
    if p[0] != "load":
        ids[p[0]] = p[1]


def row(m, i):
    r = dict(id=i, wall=None, usec=None, ok=None, why=None, gate=None)
    try:
        v = json.load(open(f"{S}/validity/{i}.json")); r.update(ok=v["ok"], why=v["why"])
    except OSError:
        pass
    for l in open(f"{S}/own/{i}") if os.path.exists(f"{S}/own/{i}") else []:
        if l.startswith("usage_usec"):
            r["usec"] = int(l.split()[1])
    for d in os.listdir(O):
        if d.startswith(f"ser{m}-d15"):
            t = open(f"{O}/{d}/metrics.txt").read()
            for x in t.splitlines():
                if x.startswith("wall_s"):
                    r["wall"] = float(x.split()[1])
                if x.startswith("gate files"):
                    r["gate"] = x
    return r


rows = {m: row(m, i) for m, i in ids.items()}
print(json.dumps(rows, ensure_ascii=False, indent=1))
dl, de = [], []
for k in (1, 2, 3):
    for p, acc in (("L", dl), ("E", de)):
        a, b = rows.get(f"{p}{k}a"), rows.get(f"{p}{k}b")
        if a and b and a["usec"] and b["usec"]:
            dwall = 100 * (a["wall"] / b["wall"] - 1) if a["wall"] and b["wall"] else None
            du = 100 * (a["usec"] / b["usec"] - 1)
            both = bool(a["ok"] and b["ok"])
            print(f"{p}{k}: usage_usec {du:+.3f} %, wall {dwall if dwall is None else round(dwall, 3)} %, ok обеих сторон: {both}")
            if both:
                acc.append((abs(du), [a["usec"], b["usec"]], abs(dwall) if dwall is not None else None))
for n, acc in (("нагрузка", dl), ("пусто", de)):
    print(f"{n}: пар с ok:true×2 = {len(acc)}; max |Δusage| = {max((x[0] for x in acc), default=None)}; max |Δwall| = {max((x[2] for x in acc if x[2] is not None), default=None)}")
ml = [sum(x[1]) / 2 for x in dl]; me = [sum(x[1]) / 2 for x in de]
if ml and me:
    print(f"смещение нагрузка−пусто по среднему usage_usec: {100 * (sum(ml) / len(ml) / (sum(me) / len(me)) - 1):+.3f} %")
