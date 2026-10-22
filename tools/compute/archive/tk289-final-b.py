#!/usr/bin/env python3
"""TK-289: финал Б П-18 (§5, П-14 §5): ОДИН тест центра плато на семью на половине Б монет (нечётный sha256(symbol)), янв–сен, парная ΔSR к B1 по суткам UTC,
циклический блочный бутстреп (B=20000, seed 18 — P-18-scope.json), односторонний; α = 0,05, Холм по m семей (m фиксирован до счёта; печатаются m = 17 [6в, Судья 19.10] и m = 16 [JSON охвата]);
семья без плато / «не определена» (сделок Б < 20 или суток < 10) — p = 1. Плюс: август и сентябрь отдельно (ΔSR > 0 при ≥ 20 сделках Б в месяце, p месяца — справочно),
C2 (ΔSR > 0 без двух суток с наибольшей парной разностью и без монеты с наибольшим положительным вкладом). Центры — tk289-final-b-centers.py (только А, до открытия Б).
  python tk289-final-b.py <centers.csv> <tk0289-dir> <out-prefix> [--boot 20000]
-> <out>.csv (строка на семью), <out>.json (то же + месяцы), печать."""
import csv, datetime, hashlib, json, math, os, re, sys
import numpy as np

cen_f, root, outp = sys.argv[1:4]
B = int(sys.argv[sys.argv.index("--boot") + 1]) if "--boot" in sys.argv else 20000
SEED, ALPHA, MIN_TR, MIN_D = 18, 0.05, 20, 10
FAMS = ["1", "2", "3", "4", "5", "6a", "6b", "7", "8", "6v", "stack"]     # осей 9 + 6в; пар 6 (не считались: нет плато у обеих осей) — p = 1; стопка
NPAIRS = 6
isA = lambda s: int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0
d0 = datetime.date(2026, 1, 1); ND = (datetime.date(2026, 10, 1) - d0).days
UTC = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()
MONTHS = [f"2026-{m:02d}" for m in range(1, 10)]
TAIL = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
B1 = "t-bid-btc4h-q1@" + TAIL
KEY = {"1": "to", "2": "stop", "3": "take", "4": "deadline", "5": "ttl", "6a": "usd_min", "6b": "strength_min", "6v": "flow+usd_min", "7": "btc", "8": "sigma_min", "stack": "stack"}
RUNS = ["f1", "f6b", "f6v", "f2", "f245", "f3", "f3ttl"] + [f"f8/{w}" for w in (60, 90, 120, 180, 360, 480, 720, 960)] + ["f2b/b6"] + [f"f2b/{w}" for w in (266, 294, 325, 387, 416, 447)]


def vname(ax, v):    # как p18_plan.cell / tk289-sieve.vname
    return "p18a%sv%s" % (ax, re.sub(r"[^0-9A-Za-z]", "_", v.replace("-", "m")))


cen = [r for r in csv.DictReader(open(cen_f, encoding="utf-8"))]
need = {}
for r in cen:
    if r["hash"]:
        a = json.loads(r["axes"]); need[r["family"]] = vname(a["ax"], a[KEY[a["ax"]]])
b1c = {mo: json.load(open(f"/data/p12r3a/closes-sym-b1-cap0-{mo}.json")).get(B1, {}).get(mo, {}).get("0", []) for mo in MONTHS}
rows = {}    # (имя варианта, мес) -> [[мс, $, монета]]
for run in RUNS:
    if not os.path.isdir(f"{root}/{run}/psim"): continue
    for mo in MONTHS:
        d = json.load(open(f"{root}/{run}/psim/closes-sym-p14f1-cap0-{mo}.json"))
        for k, v in d.items():
            pre, form = k.split("@", 1)
            if any(pre == f"{n}-{mo}" for n in need.values()): rows.setdefault((pre, mo), {})[form] = v.get(mo, {}).get("0", [])


def series(rws, skip_coin=None):
    day = np.zeros(ND); has = np.zeros(ND, bool); n = 0
    for ms, usd, sym in rws:
        if isA(sym) or sym == skip_coin: continue     # половина Б
        i = (UTC(ms) - d0).days
        if 0 <= i < ND: day[i] += usd; has[i] = True; n += 1
    return day, n, int(has.sum())


sr = lambda x: x.mean(axis=-1) / np.maximum(x.std(axis=-1, ddof=1), 1e-12)


def boot_idx(nd):
    blk = math.ceil(nd ** (1 / 3)); nb = math.ceil(nd / blk)
    st = np.random.default_rng(SEED).integers(0, nd, size=(B, nb))
    return ((st[:, :, None] + np.arange(blk)[None, None, :]) % nd).reshape(B, -1)[:, :nd]


def test(x, b, idx):
    dsr = float(sr(x) - sr(b))
    if dsr <= 0: return dsr, 1.0
    db = sr(x[idx]) - sr(b[idx])
    return dsr, float((1 + np.sum((db - dsr) >= dsr)) / (B + 1))


IDX = boot_idx(ND)
b1_rw = [r for mo in MONTHS for r in b1c[mo]]
b1x, b1n, b1d = series(b1_rw)
print(f"B1 Б, янв–сен: сделок {b1n}, суток {b1d}, SR {sr(b1x):.4f}, B={B} seed {SEED}")
mon_slice = {mo: (int(mo[5:]) - 1) for mo in MONTHS}
dstart = [(datetime.date(2026, m, 1) - d0).days for m in range(1, 11)]
out = []
for r in cen:
    fam = r["family"]
    if not r["hash"]:
        out.append({"family": fam, "tested": 0, "p": 1.0, "note": r["note"]}); continue
    nm = need[fam]; bym = {}
    for mo in MONTHS:
        forms = rows.get((f"{nm}-{mo}", mo))
        if forms is None: bym[mo] = []; continue
        assert len(forms) == 1, (nm, mo, list(forms))
        bym[mo] = next(iter(forms.values()))
    if not any(bym.values()): raise SystemExit(f"нет closes: {nm}")
    allr = [t for mo in MONTHS for t in bym[mo]]
    x, n, nd = series(allr); dsr, p = test(x, b1x, IDX); ok = n >= MIN_TR and nd >= MIN_D
    d = {"family": fam, "tested": 1, "hash": r["hash"], "axes": r["axes"], "trades_B": n, "days_B": nd, "dSR": round(dsr, 4), "p_raw": round(p if ok else 1.0, 5), "defined": int(ok),
         "dSR_A": r["dSR_A"], "p_A": r["p_A"]}
    # август и сентябрь отдельно (В-115): знак ΔSR месяца при ≥ 20 сделках Б; p месяца — справочно (тот же бутстреп по суткам месяца)
    mons = {}
    for mo in MONTHS:
        mi = mon_slice[mo]; lo, hi = dstart[mi], dstart[mi + 1]
        xm, nm_, _ = series(bym[mo]); bm, bn_, _ = series(b1c[mo])
        xs, bs = xm[lo:hi], bm[lo:hi]
        dm = float(sr(xs) - sr(bs)); pm = test(xs, bs, boot_idx(hi - lo))[1] if (hi - lo) > 3 else 1.0
        mons[mo] = {"n": nm_, "usd": round(float(xm.sum()), 2), "b1_n": bn_, "b1_usd": round(float(bm.sum()), 2), "dSR": round(dm, 4), "p": round(pm, 4)}
    d["months"] = mons
    d["aug_ok"] = int(mons["2026-08"]["dSR"] > 0 and mons["2026-08"]["n"] >= MIN_TR); d["sep_ok"] = int(mons["2026-09"]["dSR"] > 0 and mons["2026-09"]["n"] >= MIN_TR)
    # C2: без двух суток с наибольшей парной разностью и без монеты с наибольшим положительным вкладом
    diff = x - b1x; top2 = np.argsort(-diff)[:2]; keep = np.ones(ND, bool); keep[top2] = False
    d["dSR_no2days"] = round(float(sr(x[keep]) - sr(b1x[keep])), 4)
    contrib = {}
    for ms, usd, sym in allr:
        if not isA(sym): contrib[sym] = contrib.get(sym, 0.0) + usd
    for ms, usd, sym in b1_rw:
        if not isA(sym): contrib[sym] = contrib.get(sym, 0.0) - usd
    topc = max(contrib, key=contrib.get)
    xc, _, _ = series(allr, topc); bc, _, _ = series(b1_rw, topc)
    d["top_coin"] = topc; d["top_coin_usd"] = round(contrib[topc], 2); d["dSR_no_coin"] = round(float(sr(xc) - sr(bc)), 4)
    d["c2_ok"] = int(d["dSR_no2days"] > 0 and d["dSR_no_coin"] > 0)
    d["note"] = r["note"]
    out.append(d)


def holm(ps, m):    # ps: p семей (недостающие до m — 1.0); возвращает скорректированные p (Холм, монотонно) для переданных
    allp = sorted(list(ps.values()) + [1.0] * (m - len(ps)))
    order = sorted(ps, key=lambda f: ps[f]); adj, run = {}, 0.0
    for k, f in enumerate(order):
        run = max(run, min(1.0, ps[f] * (m - k))); adj[f] = run
    return adj


ps = {d["family"]: d["p_raw"] if d.get("tested") else 1.0 for d in out}
for m in (17, 16):
    adj = holm(ps, m)
    for d in out: d[f"p_holm_m{m}"] = round(adj[d["family"]], 5)
for d in out:
    d["pass_m17"] = int(d.get("tested") == 1 and d["defined"] and d["p_holm_m17"] <= ALPHA and d["aug_ok"] and d["sep_ok"] and d["c2_ok"])
    d["pass_m16"] = int(d.get("tested") == 1 and d["defined"] and d["p_holm_m16"] <= ALPHA and d["aug_ok"] and d["sep_ok"] and d["c2_ok"])
cols = ["family", "tested", "hash", "axes", "trades_B", "days_B", "dSR", "p_raw", "p_holm_m17", "p_holm_m16", "aug_ok", "sep_ok", "c2_ok", "pass_m17", "pass_m16", "dSR_no2days", "top_coin", "top_coin_usd", "dSR_no_coin", "dSR_A", "p_A", "note"]
with open(outp + ".csv", "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(cols)
    for d in out: w.writerow([d.get(c, "") for c in cols])
json.dump({"B1_B": {"trades": b1n, "days": b1d, "SR": round(float(sr(b1x)), 4)}, "boot": B, "seed": SEED, "alpha": ALPHA, "families": out, "pairs": f"{NPAIRS} пар не считались (нет плато у обеих осей пары) — p = 1"},
          open(outp + ".json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
for d in out:
    if not d.get("tested"): print(f"{d['family']:>6}: не тестируется — {d['note']} (p = 1)"); continue
    print(f"{d['family']:>6}: Б сделок {d['trades_B']} суток {d['days_B']} ΔSR {d['dSR']:+.4f} p {d['p_raw']:.4f} Холм m17 {d['p_holm_m17']:.4f} m16 {d['p_holm_m16']:.4f} | авг {d['months']['2026-08']['dSR']:+.4f} (n {d['months']['2026-08']['n']}, p {d['months']['2026-08']['p']}) "
          f"сен {d['months']['2026-09']['dSR']:+.4f} (n {d['months']['2026-09']['n']}, p {d['months']['2026-09']['p']}) | C2: без 2 суток {d['dSR_no2days']:+.4f}, без {d['top_coin']} {d['dSR_no_coin']:+.4f} | проход m17 {d['pass_m17']} m16 {d['pass_m16']}")
    print("        месяцы ΔSR:", " ".join(f"{mo[5:]}:{v['dSR']:+.3f}/{v['n']}" for mo, v in d["months"].items()))
print(f"B1 Б месяцы n: " + " ".join(f"{mo[5:]}:{sum(1 for t in b1c[mo] if not isA(t[2]))}" for mo in MONTHS))
print(f"семей в Холме: m=17 (9 осей + 6в + 6 пар + стопка), протестировано {sum(d.get('tested', 0) for d in out)}, остальные p = 1")
