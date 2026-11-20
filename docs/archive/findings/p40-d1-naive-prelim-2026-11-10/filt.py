"""полные спеки: у актива готовы все 4 варианта (base, drain, k2, k1p60) ключевых файлов; пишет specs*.txt и список активов"""
import os, sys, collections
O = "/root/p40core/out_d1v4"; OUT = sys.argv[1]
V = {"": "specs.txt", "_drain": "specs_drain.txt", "_k2": "specs_k2.txt", "_k1p60": "specs_k1p60.txt"}
NEED = ["", ".exits.csv", ".path.csv", ".fills.csv", ".features.csv", ".aux.csv", ".age.csv"]
S = {v: [l.strip() for l in open(f"{O}/{f}") if l.strip()] for v, f in V.items()}
def ok(sp):
    base = sp.split(":", 4)[4]
    return all(os.path.exists(base + n) and os.path.getsize(base + n) > 0 for n in NEED[:1]) and all(os.path.exists(base + n) for n in NEED[1:])
byk = collections.defaultdict(dict)   # (asset,pair) -> {v: spec}
for v, L in S.items():
    for sp in L:
        a, d, p = sp.split(":")[:3]
        byk[(a, d, p)][v] = sp
good = {k: s for k, s in byk.items() if len(s) == 4 and all(ok(x) for x in s.values())}
bad = len(byk) - len(good)
assets_all = {k[0] for k in byk}; assets_ok = {k[0] for k in good}
for v, f in V.items():
    open(f"{OUT}/{f}", "w").write("\n".join(good[k][v] for k in sorted(good)) + "\n")
print("пар-кусков всего", len(byk), "готово", len(good), "неполных", bad, "активов в specs", len(assets_all), "с готовыми парами", len(assets_ok))
