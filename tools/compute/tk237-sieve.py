#!/usr/bin/env python3
"""TK-237: сито Q-1 П-14 по готовым closes с монетой — клетка, половина А, янв–сен (плато — отдельный шаг).
ΔSR = SR(клетка) − SR(B1) по календарным суткам UTC (все сутки окна, без сделок = 0), парный блочный бутстреп по суткам
(циклический, блок ⌈n^{1/3}⌉), p односторонний по центрированному Δ*. Годна: p ≤ 0,20, сделок ≥ 20, суток со сделками ≥ 10.
m (равная экспозиция) в closes нет => семьи с m ≠ 1 (Г-87/92/93/95) — «не определена (m)».
Вселенная: из клеток и B1 вырезаются монето-сутки, которые есть в клетке с входом B1 (…sw10), но нет в B1 (деревья B1 и F1 расходятся).
  python tk237-sieve.py <closes-dir> <keymap.json> <coverage.csv> <out.csv> [--boot 4000] [--monthly <out-monthly.csv>]
"""
import csv, hashlib, json, math, sys, datetime
import numpy as np
cdir, kmap, cov, out = sys.argv[1:5]
B = int(sys.argv[sys.argv.index("--boot") + 1]) if "--boot" in sys.argv else 4000
B1 = "t-bid-btc4h-q1@ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
M_NOT_1 = {"Г-87", "Г-92", "Г-93", "Г-95"}
isA = lambda s: int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0
d0 = datetime.date(2026, 1, 1); ND = (datetime.date(2026, 9, 30) - d0).days + 1
keymap = json.load(open(kmap, encoding="utf-8"))
allc = list(csv.DictReader(open(cov, encoding="utf-8")))   # новые F1 + старые клетки, у которых есть sym-closes (scope=old)
closes = {}
for m in range(1, 10):
    mo = f"2026-{m:02d}"; closes[mo] = json.load(open(f"{cdir}/closes-sym-f1-cap0-{mo}.json"))
    import os
    if os.path.exists(f"{cdir}/closes-sym-r2b-cap0-{mo}.json"):   # 31 клетка R2-B (старые, сито «плато»): tk237-r2b-symclose.sh
        for k, v in json.load(open(f"{cdir}/closes-sym-r2b-cap0-{mo}.json", encoding="utf-8")).items(): closes[mo].setdefault(k, v)
    closes[mo][B1] = json.load(open(f"{cdir}/closes-sym-b1-cap0-{mo}.json")).get(B1, {})   # B1 отдельным прогоном (TK-191, деревья b1-b)
UTC = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()
EQ = "ladder3x0..0.0409sw10-pct2-tr1x1-14400-ttl1800"; EXCL = set()
for mo in closes:
    eq = next(k for k in closes[mo] if k.startswith("t-bid-btc4h-q1@") and k.endswith(EQ))
    cd = lambda c: {(sym, UTC(ms)) for ms, u, sym in c}
    EXCL |= cd(closes[mo][eq].get(mo, {}).get("0", [])) - cd(closes[mo][B1].get(mo, {}).get("0", []))
print("вырезано монето-суток (есть в клетке-эквиваленте B1, нет в B1):", sorted((s_, str(d)) for s_, d in EXCL))
have = lambda h: h in keymap and keymap[h]["keys"] and all(k in closes[mo] for mo, k in keymap[h]["keys"].items())
new = [r for r in allc if r["f1_new"] == "1" or have(r["hash"])]
def series(key_by_month):
    """-> (сутки[ND] $, сделок, суток со сделками, по месяцам: [(сделок, $)]) только половина А"""
    day = np.zeros(ND); has = np.zeros(ND, bool); n = 0; bm = {}
    for mo, key in key_by_month.items():
        c = closes[mo].get(key, {}).get(mo, {}).get("0", []); nm = 0; sm = 0.0   # формат: {вариант: {эпоха: {cap: [[мс, $, монета]]}}}
        for ms, usd, sym in c:
            if not isA(sym) or (sym, UTC(ms)) in EXCL: continue
            i = (UTC(ms) - d0).days
            if 0 <= i < ND:
                day[i] += usd; has[i] = True; n += 1; nm += 1; sm += usd
        bm[mo] = (nm, sm)
    return day, n, int(has.sum()), bm
b1_keys = {f"2026-{m:02d}": B1 for m in range(1, 10)}
b1, b1n, b1d, _ = series(b1_keys)
sr = lambda x: x.mean(axis=-1) / np.maximum(x.std(axis=-1, ddof=1), 1e-12)
blk = math.ceil(ND ** (1 / 3)); nb = math.ceil(ND / blk)
rng = np.random.default_rng(14)
starts = rng.integers(0, ND, size=(B, nb))
idx = ((starts[:, :, None] + np.arange(blk)[None, None, :]) % ND).reshape(B, -1)[:, :ND]
b1_b = sr(b1[idx]); sr_b1 = sr(b1)
rows = []; se_all = []; mrows = []
_, _, _, b1m = series(b1_keys)
for r in new:
    h = r["hash"]; hyp = r["hyp"]
    if h not in keymap or not keymap[h]["keys"]:
        rows.append([hyp, h, r["axes"], "", "", "", "", "", "не определена: нет ключа"]); continue
    x, n, nd, bm = series(keymap[h]["keys"])
    dsr = sr(x) - sr_b1
    db = sr(x[idx]) - b1_b                      # парный: те же индексы
    se = db.std(ddof=1); se_all.append(se)
    p = (1 + np.sum((db - dsr) >= dsr)) / (B + 1) if dsr > 0 else 1.0
    ok = n >= 20 and nd >= 10
    m1 = hyp not in M_NOT_1
    st = ("не определена: m не применён (нет нотионала в closes)" if not m1 else "не определена: сделок<20 или суток<10" if not ok
          else "годна" if p <= 0.20 else "не годна")
    if hyp == "Г-117": st = "не определена: семья неполна (47/56)" if st == "не годна" else st
    mrows.append([hyp, h] + [v for mo in sorted(bm) for v in (bm[mo][0], round(bm[mo][1], 2))])
    rows.append([hyp, h, r["axes"], n, nd, round(float(dsr), 4), round(float(p), 4), round(float(se), 4), st])
with open(out, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["hyp", "hash", "axes", "trades_A", "days_A", "dSR", "p", "se", "status"]); w.writerows(rows)
if "--monthly" in sys.argv:   # по месяцам: сделок и $ клетки; Δ бп на сделку к B1 при позиции 500 $ (portfolio-sim --position-usd 500)
    mo_l = sorted(b1m); hd = ["hyp", "hash"] + [f"{mo}_{k}" for mo in mo_l for k in ("n", "usd")]
    hd += [f"{mo}_dbp_per_trade" for mo in mo_l]
    with open(sys.argv[sys.argv.index("--monthly") + 1], "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f); w.writerow(hd + ["b1_" + k for mo in mo_l for k in (mo + "_n", mo + "_usd")])
        for r in mrows:
            if r[-1] == "": pass
            n_u = [(r[2 + 2 * i], r[3 + 2 * i]) for i in range(len(mo_l))]
            dbp = [round((u / n - b1m[mo][1] / b1m[mo][0]) / 500 * 1e4, 1) if n and b1m[mo][0] else "" for (n, u), mo in zip(n_u, mo_l)]
            w.writerow(r + dbp + [v for mo in mo_l for v in b1m[mo]])
se_m = float(np.median(se_all))
print(f"B1 половина А: сделок {b1n}, суток {b1d}, SR(сутки) {sr_b1:.4f}, блок {blk}, B={B}")
print(f"достижимость: медиана SE(ΔSR) {se_m:.4f}; мин. обнаруживаемая ΔSR при p≤0,20 (80% мощности) ≈ {1.68*se_m:.4f}; порог p≤0,20 ≈ {0.84*se_m:.4f}")
import collections
print(collections.Counter(r[-1].split(":")[0] for r in rows))
print(collections.Counter((r[0], r[-1].split(":")[0]) for r in rows))
