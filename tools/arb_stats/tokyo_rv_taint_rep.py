import pickle
from collections import defaultdict
d = pickle.load(open("/data/arb-tokyo-review/taint.pkl", "rb"))
wd = (d["t1"] - d["t0"]) / 86_400_000
a = defaultdict(lambda: [0.0, 0.0, 0, 0, 0.0, 0])
for (c, x, y, th, lat, nm), v in d["rows"].items():
    for k in ((th, lat, nm, "all"), (th, lat, nm, f"{x}-{y}")):
        for i in range(6):
            a[k][i] += v[i]
for k in sorted(a):
    if k[3] != "all": continue
    t, ta, na, n, fu, fnan = a[k]
    print(f"θ={k[0]/100:g} L={k[1]} {k[2]}: $/сут {t/wd:+.0f}; аудит {ta/wd:+.0f} ({na} кругов из {n}); фандинг {fu/wd:+.0f} $/сут; без истории фандинга {fnan}")
