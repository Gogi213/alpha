#!/usr/bin/env python3
"""TK-237: сито Q-1 П-14 по готовым closes с монетой — клетка, половина А, янв–сен (плато — отдельный шаг).
ΔSR = SR(клетка) − SR(B1) по календарным суткам UTC (все сутки окна, без сделок = 0), парный блочный бутстреп по суткам
(циклический, блок ⌈n^{1/3}⌉), p односторонний по центрированному Δ*. Годна: p ≤ 0,20, сделок ≥ 20, суток со сделками ≥ 10.
m (равная экспозиция) в closes нет => семьи с m ≠ 1 (Г-87/92/93/95) — только справочно.
  python tk237-sieve.py <closes-dir> <keymap.json> <coverage.csv> <out.csv> [--boot 4000]"""
import csv, hashlib, json, math, sys, datetime
import numpy as np
cdir, kmap, cov, out = sys.argv[1:5]
B = int(sys.argv[sys.argv.index("--boot") + 1]) if "--boot" in sys.argv else 4000
B1 = "t-bid-btc4h-q1@ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
M_NOT_1 = {"Г-87", "Г-92", "Г-93", "Г-95"}
isA = lambda s: int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0
d0 = datetime.date(2026, 1, 1); ND = (datetime.date(2026, 9, 30) - d0).days + 1
keymap = json.load(open(kmap, encoding="utf-8"))
new = [r for r in csv.DictReader(open(cov, encoding="utf-8")) if r["f1_new"] == "1"]
closes = {}
for m in range(1, 10):
    mo = f"2026-{m:02d}"; closes[mo] = json.load(open(f"{cdir}/closes-sym-f1-cap0-{mo}.json"))
    closes[mo][B1] = json.load(open(f"{cdir}/closes-sym-b1-cap0-{mo}.json")).get(B1, {})   # B1 отдельным прогоном (TK-191, деревья b1-b)
def series(key_by_month):
    """-> (сутки[ND] $, сделок, суток со сделками, по месяцам: [(сделок, $)]) только половина А"""
    day = np.zeros(ND); has = np.zeros(ND, bool); n = 0; bm = {}
    for mo, key in key_by_month.items():
        c = closes[mo].get(key, {}).get(mo, {}).get("0", []); nm = 0; sm = 0.0   # формат: {вариант: {эпоха: {cap: [[мс, $, монета]]}}}
        for ms, usd, sym in c:
            if not isA(sym): continue
            i = (datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date() - d0).days
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
rows = []; se_all = []
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
    st = "годна" if (p <= 0.20 and ok and m1) else ("не определена: сделок<20 или суток<10" if not ok else ("справочно: m≠1 (нет нотионала в closes)" if (p <= 0.20 and not m1) else "не годна"))
    rows.append([hyp, h, r["axes"], n, nd, round(float(dsr), 4), round(float(p), 4), round(float(se), 4), st])
with open(out, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["hyp", "hash", "axes", "trades_A", "days_A", "dSR", "p", "se", "status"]); w.writerows(rows)
se_m = float(np.median(se_all))
print(f"B1 половина А: сделок {b1n}, суток {b1d}, SR(сутки) {sr_b1:.4f}, блок {blk}, B={B}")
print(f"достижимость: медиана SE(ΔSR) {se_m:.4f}; мин. обнаруживаемая ΔSR при p≤0,20 (80% мощности) ≈ {1.68*se_m:.4f}; порог p≤0,20 ≈ {0.84*se_m:.4f}")
import collections
print(collections.Counter(r[-1].split(":")[0] for r in rows))
print(collections.Counter((r[0], r[-1].split(":")[0]) for r in rows))
