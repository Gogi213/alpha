#!/usr/bin/env python3
"""TK-308: сито П-17 F1 (292 хеша) по готовым closes с монетой (psim cap 0, без движка) — половина А.
Метод — tk237/tk278-sieve (ΔSR по суткам UTC, парный циклический блочный бутстреп, B=4000, seed 14; годна p ≤ 0,20, сделок ≥ 20, суток ≥ 10),
плато — как registry/master.sieve (rules P-17@r2). Отличия П-17 §4: окно Г-135/136/137 — янв–июл (авг/сен «видели», в сито не входят);
Г-136: ΔSR > 0 и к B1, и к контролю (A, k=0), p по разнице с контролем; Г-98: «не определена» (R≥3 тождественны B1, §47).
  python tk308-sieve.py <registry-dir> <out-prefix> [--boot 4000]"""
import collections, csv, hashlib, itertools, json, math, sys, datetime
import numpy as np

reg, outp = sys.argv[1:3]
B = int(sys.argv[sys.argv.index("--boot") + 1]) if "--boot" in sys.argv else 4000
TAIL = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
B1 = "t-bid-btc4h-q1@" + TAIL
RUNS = {"Г-98": "p17g98", "Г-101": "p17a", "Г-81": "p17g81", "Г-135": "p17b", "Г-136": "p17b", "Г-137": "p17b"}
D_CLASS = {"Г-135", "Г-136", "Г-137"}          # окно янв–июл
isA = lambda s: int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0
d0 = datetime.date(2026, 1, 1)
UTC = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()
plan = [json.loads(l) for l in open(f"{reg}/plan_cells.jsonl", encoding="utf-8")]
plan = [r for r in plan if r["protocol"] == "P-17" and r["fraction"] == 1]
rules = {d["key"]: d["value"] for d in map(json.loads, open(f"{reg}/rules.jsonl", encoding="utf-8")) if d["version"] == "P-17@r2"}
P_MAX, MIN_TR, MIN_D, NB = float(rules["p_max"]), int(rules["min_trades"]), int(rules["min_days"]), int(rules["plateau_min_nb"])
types = {d["name"]: d["axis_type"] for d in map(json.loads, open(f"{reg}/params.jsonl", encoding="utf-8"))}


def name(hyp, a, mo):
    if hyp == "Г-98": return f"g98r{a['R_usd']}"
    if hyp == "Г-101": return f"g101{a['part']}t{a['T_q']}f{a['f_bp']}-{mo}"
    if hyp == "Г-81": return f"g81cxlw{a['W_s']}q{a['cxl_q']}-{mo}"
    if hyp == "Г-135": return f"g135q{a['swept_q']}-{mo}"
    if hyp == "Г-136": return f"g136a{a['A_min']}k{a['k']}"
    if hyp == "Г-137": return f"g137k{a['k']}"


closes = {}   # run -> mo -> {prefix: {entry_form_prefix: rows}}
b1c = {}
for m in range(1, 10):
    mo = f"2026-{m:02d}"
    b1c[mo] = json.load(open(f"/data/p12r3a/closes-sym-b1-cap0-{mo}.json")).get(B1, {}).get(mo, {}).get("0", [])
    for run in set(RUNS.values()):
        d = json.load(open(f"/data/tk0279/{run}/psim/closes-sym-p14f1-cap0-{mo}.json"))
        dd = closes.setdefault(run, {}).setdefault(mo, {})
        for k, v in d.items():
            pre, form = k.split("@", 1)
            dd.setdefault(pre, {})[form] = v.get(mo, {}).get("0", [])


def rows_of(hyp, a, mo):
    run = RUNS[hyp]; nm = name(hyp, a, mo); forms = closes[run][mo].get(nm, {})
    if hyp == "Г-135":
        return forms.get(f"{a['entry']}-pct2-tr1x1-14400-ttl1800", [])
    return forms.get(TAIL, [])


def series(rows_by_mo, nmo):
    ND = (datetime.date(2026, nmo + 1, 1) - d0).days if nmo < 12 else 365
    day = np.zeros(ND); has = np.zeros(ND, bool); n = 0
    for mo, rows in rows_by_mo.items():
        if int(mo[5:]) > nmo: continue
        for ms, usd, sym in rows:
            if not isA(sym): continue
            i = (UTC(ms) - d0).days
            if 0 <= i < ND: day[i] += usd; has[i] = True; n += 1
    return day, n, int(has.sum())


sr = lambda x: x.mean(axis=-1) / np.maximum(x.std(axis=-1, ddof=1), 1e-12)
boots = {}


def boot(ND):
    if ND not in boots:
        blk = math.ceil(ND ** (1 / 3)); nb = math.ceil(ND / blk)
        st = np.random.default_rng(14).integers(0, ND, size=(B, nb))
        boots[ND] = ((st[:, :, None] + np.arange(blk)[None, None, :]) % ND).reshape(B, -1)[:, :ND]
    return boots[ND]


def test(x, base):
    """ΔSR клетки к base и односторонний p по парному бутстрепу (как tk237)."""
    idx = boot(len(x)); dsr = sr(x) - sr(base); db = sr(x[idx]) - sr(base[idx])
    p = (1 + np.sum((db - dsr) >= dsr)) / (B + 1) if dsr > 0 else 1.0
    return float(dsr), float(p)


res = {}
for r in plan:
    h, hyp, a = r["cell_hash"], r["hyp"], json.loads(r["axes"])
    nmo = 7 if hyp in D_CLASS else 9
    bym = {f"2026-{m:02d}": rows_of(hyp, a, f"2026-{m:02d}") for m in range(1, nmo + 1)}
    x, n, nd = series(bym, nmo)
    b1x, b1n, b1d = series({mo: b1c[mo] for mo in bym}, nmo)
    dsr, p = test(x, b1x)
    st = {"h": h, "hyp": hyp, "a": a, "nmo": nmo, "x": x, "n": n, "nd": nd, "dsr": dsr, "p_b1": p, "b1n": b1n, "b1d": b1d, "b1x": b1x,
          "mon": {mo: (sum(1 for ms, u, s in rw if isA(s)), round(sum(u for ms, u, s in rw if isA(s)), 2)) for mo, rw in bym.items()}}
    res[h] = st
ctrl = {}   # Г-136: A -> контроль (k=0)
for st in res.values():
    if st["hyp"] == "Г-136" and st["a"]["k"] == "0": ctrl[st["a"]["A_min"]] = st
for st in res.values():
    hyp = st["hyp"]; p = st["p_b1"]; dsr = st["dsr"]; extra = ""
    if hyp == "Г-136":
        c = ctrl[st["a"]["A_min"]]
        if st["a"]["k"] == "0": st["status"] = "контроль (k=0)"; st["p"] = 1.0; continue
        d2, p2 = test(st["x"], c["x"])
        extra = f"к контролю ΔSR {d2:+.4f} p {p2:.3f}"
        p = p2 if (dsr > 0 and d2 > 0) else 1.0
    st["extra"] = extra; st["p"] = p
    ok = st["n"] >= MIN_TR and st["nd"] >= MIN_D
    if hyp == "Г-98": st["status"] = "не определена: R≥3 тождественны B1 (П-17 §47: у ≥95 % касаний лот 500 $)"; st["ok"] = 0; continue
    st["ok"] = int(ok and p <= P_MAX)
    st["status"] = "не определена: сделок<20 или суток<10" if not ok else "годна" if p <= P_MAX else (
        "не годна: молодые стены лучше сами (контроль)" if hyp == "Г-136" and dsr > 0 and p == 1.0 and st["p_b1"] <= P_MAX else "не годна")
for st in res.values(): st.setdefault("ok", 0)
# плато — как master.sieve
fval = lambda v: (0, float(v)) if v.replace(".", "", 1).replace("-", "", 1).isdigit() else (1, v)
by = collections.defaultdict(list)
for st in res.values(): by[st["hyp"]].append(st)
for hyp, cells in by.items():
    names = list(cells[0]["a"]); num = [a for a in names if types.get(a, "numeric") != "categorical"]; cat = [a for a in names if types.get(a) == "categorical"]
    pos = {a: {v: i for i, v in enumerate(sorted({c["a"][a] for c in cells}, key=fval))} for a in num}
    grid = {(tuple(c["a"][a] for a in cat), tuple(pos[a][c["a"][a]] for a in num)): c for c in cells}
    for c in cells:
        c["plateau"] = c["edge"] = 0
        if not c["ok"] or not num: continue
        cat_v = tuple(c["a"][a] for a in cat); ix = tuple(pos[a][c["a"][a]] for a in num); nbs = []
        for d in itertools.product((-1, 0, 1), repeat=len(num)):
            if not any(d): continue
            j = tuple(i + di for i, di in zip(ix, d))
            if all(0 <= jj < len(pos[a]) for jj, a in zip(j, num)) and (cat_v, j) in grid: nbs.append(grid[(cat_v, j)])
        full = 3 ** len(num) - 1; good = sum(x["ok"] for x in nbs)
        if len(num) == 1: c["edge"] = int(len(nbs) == 1); c["plateau"] = int(good == len(nbs) and len(nbs) >= 1)
        else: c["edge"] = int(len(nbs) < full); c["plateau"] = int(good >= NB if len(nbs) == full else good * 2 > len(nbs))
rows = sorted(res.values(), key=lambda s: (s["hyp"], s["h"]))
mos = [f"2026-{m:02d}" for m in range(1, 10)]
with open(outp + ".csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["hyp", "hash", "axes", "window", "trades_A", "days_A", "dSR_B1", "p", "eligible", "plateau", "edge", "итог", "к контролю"] + [f"{mo}_n" for mo in mos] + [f"{mo}_usd" for mo in mos])
    for s in rows:
        w.writerow([s["hyp"], s["h"], json.dumps(s["a"], ensure_ascii=False), "янв–июл" if s["nmo"] == 7 else "янв–сен", s["n"], s["nd"], round(s["dsr"], 4), round(s["p"], 4),
                    s["ok"], s["plateau"], s["edge"], s["status"], s.get("extra", "")] + [s["mon"].get(mo, ("", ""))[0] for mo in mos] + [s["mon"].get(mo, ("", ""))[1] for mo in mos])
with open(outp + "-rows.json", "w", encoding="utf-8") as f:
    json.dump([{"protocol": "P-17", "fraction": 1, "hyp": s["hyp"], "cell_hash": s["h"], "side": "A", "p": round(s["p"] if s["ok"] else 1.0, 4), "trades": s["n"], "days": s["nd"],
                "eligible": s["ok"], "plateau": s["plateau"], "edge": s["edge"], "rule_version": "P-17@r2"} for s in rows], f, ensure_ascii=False)
for nmo in (7, 9):
    s0 = next(s for s in rows if s["nmo"] == nmo)
    print(f"B1 А, окно {nmo} мес.: сделок {s0['b1n']}, суток {s0['b1d']}, SR {sr(s0['b1x']):.4f}")
tab = collections.defaultdict(collections.Counter)
for s in rows: tab[s["hyp"]][s["status"].split(":")[0] + (" плато" if s["plateau"] else "")] += 1
for h, c in sorted(tab.items()): print(h, dict(c))
print("годных", sum(s["ok"] for s in rows), "плато", sum(s["plateau"] for s in rows), "клеток", len(rows))
