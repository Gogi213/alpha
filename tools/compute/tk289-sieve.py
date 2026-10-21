#!/usr/bin/env python3
"""TK-289: сито П-18 F1 (163 клетки по 9 осям) по готовым closes с монетой (psim cap 0, tk289-symclose.sh) — половина А, янв–сен.
Метод — tk308-sieve (ΔSR к B1 по суткам UTC, парный циклический блочный бутстреп, B=4000, seed 14; годна p ≤ 0,20, сделок ≥ 20, суток ≥ 10).
Плато (П-14 §4 п. 4): 1D — клетка и оба соседа годны, на краю — единственный сосед годен + edge; центр B1 — точка оси без ΔSR (не годна);
2D (ось 6в, S × usd_min) — ≥ 5 из 8 соседей годны, на краю — большинство существующих. Клетки 6а/6б вложены — печатается доля общих сделок соседей не здесь.
  python tk289-sieve.py <plan_cells.jsonl> <tk0289-dir> <out-prefix> [--boot 4000] [--old <tk237-sieve-f1-cell-A.csv>]
Клетки оси 5 с ttl 300…1200 — это Г-129 F1 (TK-237, тот же метод и окно): в closes П-18 их нет, строка берётся из --old по хешу.
  -> <out>.csv (клетки), <out>-rows.json (строки для sieve.jsonl), печать: B1 А, итог по осям."""
import collections, csv, hashlib, itertools, json, math, os, sys, datetime
import numpy as np

plan_f, root, outp = sys.argv[1:4]
B = int(sys.argv[sys.argv.index("--boot") + 1]) if "--boot" in sys.argv else 4000
OLD = {r["hash"]: r for r in csv.DictReader(open(sys.argv[sys.argv.index("--old") + 1], encoding="utf-8"))} if "--old" in sys.argv else {}
P_MAX, MIN_TR, MIN_D, NB = 0.20, 20, 10, 5
RULE = "P-18@F1"
TAIL = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
B1 = "t-bid-btc4h-q1@" + TAIL
isA = lambda s: int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0
d0 = datetime.date(2026, 1, 1); ND = (datetime.date(2026, 10, 1) - d0).days
UTC = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]
RUNS = ["f1", "f6b", "f6v"] + [f"f8/{w}" for w in (60, 90, 120, 180, 360, 480, 720, 960)]
CENTER = {"1": 0.0409, "2": 2.0, "3": 1.0, "4": 14400.0, "5": 1800.0, "6a": 0.0, "6b": 0.0, "7": -44.55, "8": 240.0}   # центр B1 на числовой оси
KEY = {"1": "to", "2": "stop", "3": "take", "4": "deadline", "5": "ttl", "6a": "usd_min", "6b": "strength_min", "6v": "flow+usd_min", "7": "btc", "8": "sigma_min"}


def num(ax, v):
    """значение оси -> число для порядка (none у btc — «без фильтра», дальше −126; у usd_min/6в — 0)"""
    if ax == "7" and v == "none": return -1e9
    if v.startswith("pct"): return float(v[3:])
    if v.startswith("tr"): return float(v[2:].split("x")[0])
    return float(v)


def vname(ax, v):    # как p18_plan.cell: имя варианта без месяца
    import re
    return "p18a%sv%s" % (ax, re.sub(r"[^0-9A-Za-z]", "_", v.replace("-", "m")))


plan = [json.loads(l) for l in open(plan_f, encoding="utf-8")]
plan = [r for r in plan if r["protocol"] == "P-18" and r["fraction"] == 1]
b1c = {mo: json.load(open(f"/data/p12r3a/closes-sym-b1-cap0-{mo}.json")).get(B1, {}).get(mo, {}).get("0", []) for mo in MONTHS}
rows = {}    # (имя варианта, мес) -> [[мс, $, монета]]
for run in RUNS:
    for mo in MONTHS:
        d = json.load(open(f"{root}/{run}/psim/closes-sym-p14f1-cap0-{mo}.json"))
        for k, v in d.items():
            pre, form = k.split("@", 1)
            rows.setdefault((pre, mo), {})[form] = v.get(mo, {}).get("0", [])


def series(rows_by_mo):
    day = np.zeros(ND); has = np.zeros(ND, bool); n = 0
    for mo, rw in rows_by_mo.items():
        for ms, usd, sym in rw:
            if not isA(sym): continue
            i = (UTC(ms) - d0).days
            if 0 <= i < ND: day[i] += usd; has[i] = True; n += 1
    return day, n, int(has.sum())


sr = lambda x: x.mean(axis=-1) / np.maximum(x.std(axis=-1, ddof=1), 1e-12)
blk = math.ceil(ND ** (1 / 3)); nb = math.ceil(ND / blk)
st_ = np.random.default_rng(14).integers(0, ND, size=(B, nb))
IDX = ((st_[:, :, None] + np.arange(blk)[None, None, :]) % ND).reshape(B, -1)[:, :ND]
b1x, b1n, b1d = series(b1c)


def test(x):
    dsr = sr(x) - sr(b1x); db = sr(x[IDX]) - sr(b1x[IDX])
    return float(dsr), float((1 + np.sum((db - dsr) >= dsr)) / (B + 1)) if dsr > 0 else 1.0


res = []
for r in plan:
    a = json.loads(r["axes"]); ax = a["ax"]; val = a[KEY[ax]]; nm = vname(ax, val)
    bym = {}
    if r["cell_hash"] in OLD and (f"{nm}-{MONTHS[0]}", MONTHS[0]) not in rows:
        o = OLD[r["cell_hash"]]; n, nd, dsr, p = int(o["trades_A"]), int(o["days_A"]), float(o["dSR"]), float(o["p"]); ok = n >= MIN_TR and nd >= MIN_D
        res.append({"hyp": r["hyp"], "h": r["cell_hash"], "ax": ax, "val": val, "a": a, "n": n, "nd": nd, "dsr": dsr, "p": p if ok else 1.0, "ok": int(ok and p <= P_MAX),
                    "st": "не определена: сделок<20 или суток<10" if not ok else "годна" if p <= P_MAX else "не годна", "mon": {mo: ("", "") for mo in MONTHS}})
        continue
    for mo in MONTHS:
        forms = rows.get((f"{nm}-{mo}", mo))
        if forms is None:      # вариант без сделок в месяце psim в closes не пишет; нет ни в одном месяце — ошибка
            if not any((f"{nm}-{m2}", m2) in rows for m2 in MONTHS): raise SystemExit(f"нет closes: {nm}")
            bym[mo] = []; continue
        assert len(forms) == 1, (nm, mo, list(forms))
        bym[mo] = next(iter(forms.values()))
    x, n, nd = series(bym); dsr, p = test(x)
    ok = n >= MIN_TR and nd >= MIN_D
    res.append({"hyp": r["hyp"], "h": r["cell_hash"], "ax": ax, "val": val, "a": a, "n": n, "nd": nd, "dsr": dsr, "p": p if ok else 1.0, "ok": int(ok and p <= P_MAX),
                "st": "не определена: сделок<20 или суток<10" if not ok else "годна" if p <= P_MAX else "не годна",
                "mon": {mo: (sum(1 for ms, u, s in bym[mo] if isA(s)), round(sum(u for ms, u, s in bym[mo] if isA(s)), 2)) for mo in MONTHS}})

# плато
by = collections.defaultdict(list)
for c in res: by[c["ax"]].append(c)
for ax, cells in by.items():
    for c in cells: c["plateau"] = c["edge"] = 0
    if ax == "6v":
        S = sorted({num(ax, c["val"].split("|")[0]) for c in cells}); U = sorted({0.0 if c["val"].split("|")[1] == "none" else num(ax, c["val"].split("|")[1]) for c in cells})
        pos = lambda c: (S.index(num(ax, c["val"].split("|")[0])), U.index(0.0 if c["val"].split("|")[1] == "none" else num(ax, c["val"].split("|")[1])))
        grid = {pos(c): c for c in cells}
        for c in cells:
            if not c["ok"]: continue
            i, j = pos(c); nbs = [grid[(i + di, j + dj)] for di, dj in itertools.product((-1, 0, 1), repeat=2) if (di or dj) and (i + di, j + dj) in grid]
            good = sum(x["ok"] for x in nbs); c["edge"] = int(len(nbs) < 8); c["plateau"] = int(good >= NB if len(nbs) == 8 else good * 2 > len(nbs))
        continue
    pts = sorted([(num(ax, c["val"]), c) for c in cells], key=lambda t: t[0])
    cen = CENTER[ax]
    if all(abs(v - cen) > 1e-9 for v, _ in pts):
        pts.append((cen, {"ok": 0, "center": 1})); pts.sort(key=lambda t: t[0])
    for i, (v, c) in enumerate(pts):
        if c.get("center") or not c["ok"]: continue
        nbs = [pts[j][1] for j in (i - 1, i + 1) if 0 <= j < len(pts)]
        c["edge"] = int(len(nbs) == 1); c["plateau"] = int(all(x["ok"] for x in nbs) and len(nbs) >= 1)
res.sort(key=lambda c: (c["hyp"], c["h"]))
with open(outp + ".csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["hyp", "hash", "axes", "window", "trades_A", "days_A", "dSR_B1", "p", "eligible", "plateau", "edge", "итог"] + [f"{mo}_n" for mo in MONTHS] + [f"{mo}_usd" for mo in MONTHS])
    for c in res:
        w.writerow([c["hyp"], c["h"], json.dumps(c["a"], ensure_ascii=False), "янв–сен", c["n"], c["nd"], round(c["dsr"], 4), round(c["p"], 4), c["ok"], c["plateau"], c["edge"], c["st"]]
                   + [c["mon"][mo][0] for mo in MONTHS] + [c["mon"][mo][1] for mo in MONTHS])
json.dump([{"protocol": "P-18", "fraction": 1, "hyp": c["hyp"], "cell_hash": c["h"], "side": "A", "p": round(c["p"], 4), "trades": c["n"], "days": c["nd"], "eligible": c["ok"],
            "plateau": c["plateau"], "edge": c["edge"], "rule_version": RULE} for c in res], open(outp + "-rows.json", "w", encoding="utf-8"), ensure_ascii=False)
print(f"B1 А, янв–сен: сделок {b1n}, суток {b1d}, SR {sr(b1x):.4f}")
tab = collections.defaultdict(collections.Counter)
for c in res: tab[c["hyp"]][c["st"].split(":")[0] + (" плато" if c["plateau"] else "")] += 1
for h, cn in sorted(tab.items()): print(h, dict(cn))
print("годных", sum(c["ok"] for c in res), "плато", sum(c["plateau"] for c in res), "клеток", len(res))
