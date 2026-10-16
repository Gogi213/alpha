#!/usr/bin/env python3
"""TK-277 / П-16 r2: walk-forward (Г-02) по готовым посуточным closes (0 новых хеш-месяцев, В-213).
Фолды: тест апр…сен, растущее окно от 01.01 (мин. 3 мес). Процедура выбора = П-14 §11.2 п.1 (сито -> центр: мин. p, парная ΔSR к B1,
блочный бутстреп по суткам). Монеты — все. m = 9 семей. Вердикт по §7 П-16. Чтение исходов — один раз.
  python tk277-walkforward.py <f1-closes-dir> <aux-closes-dir(r2b,b1)> <keymap.json> <coverage.csv> <sums.csv> <outdir> [--boot 20000]
"""
import csv, hashlib, json, math, sys, datetime, os, statistics
import itertools
from fractions import Fraction
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))   # на calc selection_bias.py лежит рядом
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", ".claude", "skills", "alpha-research", "scripts"))
from selection_bias import deflated_sharpe, pbo   # DSR / PBO (CSCV) — печать §5
f1dir, auxdir, kmap, cov, sums, outdir = sys.argv[1:7]
B = int(sys.argv[sys.argv.index("--boot") + 1]) if "--boot" in sys.argv else 20000
METRICS = sys.argv[sys.argv.index("--metrics") + 1] if "--metrics" in sys.argv else None   # registry/metrics.jsonl Летописи
os.makedirs(outdir, exist_ok=True)
B1 = "t-bid-btc4h-q1@ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
FAM = ["Г-10", "Г-65", "Г-104", "Г-110", "Г-112", "Г-114", "Г-119", "Г-129", "Г-133"]
SEED = 16; ALPHA = 0.05
d0 = datetime.date(2026, 1, 1); ND = (datetime.date(2026, 9, 30) - d0).days + 1
UTC = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()
MON = {m: ((datetime.date(2026, m, 1) - d0).days,
           (datetime.date(2026 + (m == 12), m % 12 + 1, 1) - d0).days if m < 9 else ND) for m in range(1, 10)}
TEST = [4, 5, 6, 7, 8, 9]

keymap = json.load(open(kmap, encoding="utf-8"))
allc = list(csv.DictReader(open(cov, encoding="utf-8")))
closes = {}
for m in range(1, 10):
    mo = f"2026-{m:02d}"; closes[mo] = json.load(open(f"{f1dir}/closes-sym-f1-cap0-{mo}.json"))
    p = f"{auxdir}/closes-sym-r2b-cap0-{mo}.json"
    if os.path.exists(p):
        for k, v in json.load(open(p, encoding="utf-8")).items(): closes[mo].setdefault(k, v)
    closes[mo][B1] = json.load(open(f"{auxdir}/closes-sym-b1-cap0-{mo}.json")).get(B1, {})
EQ = "ladder3x0..0.0409sw10-pct2-tr1x1-14400-ttl1800"; EXCL = set()
for mo in closes:
    eq = next(k for k in closes[mo] if k.startswith("t-bid-btc4h-q1@") and k.endswith(EQ))
    cd = lambda c: {(sym, UTC(ms)) for ms, u, sym in c}
    EXCL |= cd(closes[mo][eq].get(mo, {}).get("0", [])) - cd(closes[mo][B1].get(mo, {}).get("0", []))
print("вырезано монето-суток:", len(EXCL))
have = lambda h: h in keymap and keymap[h]["keys"] and all(k in closes[mo] for mo, k in keymap[h]["keys"].items())
cells = {f: [r for r in allc if r["hyp"] == f and (r["f1_new"] == "1" or have(r["hash"])) and have(r["hash"])] for f in FAM}
print("клеток по семьям:", {f: len(v) for f, v in cells.items()})
scope = {"protocol": "П-16 r2", "m": len(FAM), "seed": SEED, "test_months": TEST, "boot": B, "families": {
    f: [{"hash": r["hash"], "axes": r["axes"]} for r in v] for f, v in cells.items()}}
json.dump(scope, open(f"{outdir}/P-16-scope.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)

def trades(keys, mo):
    """сделки [(сутки-индекс, $, монета)] клетки в месяце mo (все монеты, без вырезки EXCL)"""
    out = []
    for ms, usd, sym in closes[mo].get(keys[mo], {}).get(mo, {}).get("0", []):
        if (sym, UTC(ms)) in EXCL: continue
        i = (UTC(ms) - d0).days
        if 0 <= i < ND: out.append((i, usd, sym))
    return out
def daily(keys):
    day = np.zeros(ND); cnt = np.zeros(ND, int)
    for mo in keys:
        for i, usd, sym in trades(keys, mo): day[i] += usd; cnt[i] += 1
    return day, cnt
b1keys = {f"2026-{m:02d}": B1 for m in range(1, 10)}
b1, b1c = daily(b1keys)

# ворота (а): Σ $ по клетке-месяцу (до вырезки EXCL) = closes-f1-sum.csv (cap 0)
sm = {(r["variant"], r["month"]): float(r["sum_usd"]) for r in csv.DictReader(open(sums)) if r["cap"] == "0"}
chk = bad = 0
for f in FAM:
    for r in cells[f]:
        for mo, k in keymap[r["hash"]]["keys"].items():
            if (k, mo) in sm:
                chk += 1
                s = sum(u for _, u, _ in closes[mo][k].get(mo, {}).get("0", []))
                if abs(s - sm[(k, mo)]) > 0.01 + 1e-6 * abs(s): bad += 1
print(f"ворота (а): сверено клетко-месяцев {chk}, расхождений {bad}")

# ворота (а) по Летописи: Σ$ клетко-месяца (до вырезки) = metrics.jsonl by_month.pnl (cap 0); расхождения — списком, любое = стоп
lchk = 0; lbad = []; lmiss = 0
if METRICS:
    let = {}
    for l in open(METRICS, encoding="utf-8"):
        r = json.loads(l); let[r["hash"]] = r.get("by_month", {})
    cellh = {r["hash"] for f in FAM for r in cells[f]} | {"B1"}
    for f in FAM:
        for r in cells[f]:
            h = r["hash"]
            if h not in let: lmiss += 1; continue
            for mo, k in keymap[h]["keys"].items():
                if mo not in let[h]: continue
                lchk += 1
                s_ = sum(u for _, u, _ in closes[mo][k].get(mo, {}).get("0", []))
                if abs(s_ - let[h][mo]["pnl"]) > 0.02 + 1e-6 * abs(s_): lbad.append((f, h, mo, round(s_, 4), let[h][mo]["pnl"]))
    print(f"ворота (а) Летопись: сверено {lchk}, расхождений {len(lbad)}, клеток без записи в Летописи {lmiss}")
    for x in lbad[:40]: print("  расхождение", x)
# ворота (в): число клеток по семьям = таблица покрытия tk237-grid-coverage (status «полно» или f1_new = 1)
covn = {f: sum(1 for r in allc if r["hyp"] == f and (r["status"] == "полно" or r["f1_new"] == "1")) for f in FAM}
covbad = {f: (len(cells[f]), covn[f]) for f in FAM if len(cells[f]) != covn[f]}
print("ворота (в): клеток в WF / по таблице покрытия:", {f: (len(cells[f]), covn[f]) for f in FAM}, "расхождения:", covbad)

X = {}; XC = {}
for f in FAM:
    X[f] = np.zeros((len(cells[f]), ND)); XC[f] = np.zeros((len(cells[f]), ND), int)
    for j, r in enumerate(cells[f]): X[f][j], XC[f][j] = daily(keymap[r["hash"]]["keys"])

sr = lambda x: x.mean(axis=-1) / np.maximum(x.std(axis=-1, ddof=1), 1e-12)
def boot_idx(n, rng, Bn=B):
    blk = math.ceil(n ** (1 / 3)); nb = math.ceil(n / blk)
    st = rng.integers(0, n, size=(Bn, nb))
    return ((st[:, :, None] + np.arange(blk)[None, None, :]) % n).reshape(Bn, -1)[:, :n]
def pval(dsr, db):   # как сито
    return (1 + np.sum((db - dsr) >= dsr)) / (len(db) + 1) if dsr > 0 else 1.0

def axinfo(f):
    """числовые оси семьи (упорядоченные значения) и категориальные — как tk237-plateau.py"""
    lst = [json.loads(r["axes"]) for r in cells[f]]; cat = {"Г-104": {"be"}, "Г-114": {"trigger"}}.get(f, set()); num = []
    for k in sorted({k for a in lst for k in a}):
        if k in cat: continue
        try: [Fraction(a[k]) for a in lst if k in a]; num.append(k)
        except Exception: cat.add(k)
    return num, {k: sorted({a[k] for a in lst if k in a}, key=Fraction) for k in num}
AX = {f: axinfo(f) for f in FAM}
AXV = {f: [json.loads(r["axes"]) for r in cells[f]] for f in FAM}
def axdist(f, j, prev):
    """расстояние оси клетки j до прежнего центра prev (индекс клетки); фолд 1 — prev=None -> 0 (значение B1 не в сетке)"""
    if prev is None: return 0
    num, ordv = AX[f]; a, b = AXV[f][j], AXV[f][prev]
    return sum(abs(ordv[k].index(a[k]) - ordv[k].index(b[k])) for k in num) + sum(1 for k in a if k not in num and a[k] != b.get(k))
def plateau(f, j, info):
    """метка «центр на плато» (П-14 §4 п.4) по годным на обучении (p ≤ 0,20, ≥ 20 сделок, ≥ 10 суток); информация info: j -> (p, dsr, дней)"""
    num, ordv = AX[f]; good = lambda i: i in info and info[i][0] <= 0.20
    if not good(j): return "не годна"
    a = AXV[f][j]; idx = {tuple(sorted(x.items())): i for i, x in enumerate(AXV[f])}; pos = {k: ordv[k].index(a[k]) for k in num}; nb = []
    for d in itertools.product([-1, 0, 1], repeat=len(num)):
        if not any(d): continue
        b = dict(a); ok = True
        for k, dd in zip(num, d):
            q = pos[k] + dd
            if q < 0 or q >= len(ordv[k]): ok = False; break
            b[k] = ordv[k][q]
        if ok and tuple(sorted(b.items())) in idx: nb.append(idx[tuple(sorted(b.items()))])
    ng = sum(good(i) for i in nb)
    edge = len(nb) < 3 ** len(num) - 1
    if len(num) == 1: return ("плато" if ng == len(nb) and len(nb) >= 1 else "нет") + (" (у границы)" if edge and ng == len(nb) and nb else "")
    return "плато" if (ng >= 5 if not edge else ng * 2 > len(nb)) else "нет"

def select(Xf, Xcf, hashes, tr_end, seed, f=None, prev=None):
    """выбор центра по обучению [0,tr_end): только эти сутки; -> (j, p, dsr, valid-индексы, dsr/р всех)"""
    x = Xf[:, :tr_end]; c = Xcf[:, :tr_end]; bb = b1[:tr_end]
    valid = [j for j in range(len(x)) if c[j].sum() >= 20 and (c[j] > 0).sum() >= 10]
    if not valid: return None
    idx = boot_idx(tr_end, np.random.default_rng(seed)); b1b = sr(bb[idx]); sb = sr(bb)
    info = {}
    for j in valid:
        dsr = float(sr(x[j]) - sb); db = sr(x[j][idx]) - b1b
        info[j] = (pval(dsr, db), dsr, int((c[j] > 0).sum()))
    key = lambda j: (info[j][0], -info[j][1], -info[j][2])
    k0 = min(map(key, valid)); tied = [j for j in valid if key(j) == k0]   # §3: ничья по (p, ΔSR, суток) -> ближе к прежнему значению оси
    best = min(tied, key=lambda j: (axdist(f, j, prev), hashes[j]))
    TIES.append((len(tied), f))
    return best, info

# ---- фолды ----
res = {}; log = []; TIES = []
for f in FAM:
    hashes = [r["hash"] for r in cells[f]]; folds = []
    for t in TEST:
        tr_end = MON[t][0]; seed = SEED * 100 + t
        prev = folds[-1]["j"] if folds and "j" in folds[-1] else None
        s = select(X[f], XC[f], hashes, tr_end, seed, f, prev)
        if s is None: folds.append({"t": t, "none": True}); continue
        j, info = s
        # ворота (б): подмена данных тестового и поздних месяцев не меняет выбор
        Xb = X[f].copy(); Xcb = XC[f].copy()
        rs = np.random.default_rng(7); Xb[:, tr_end:] = rs.normal(0, 50, Xb[:, tr_end:].shape); Xcb[:, tr_end:] = 5
        j2, _ = select(Xb, Xcb, hashes, tr_end, seed, f, prev)
        assert j2 == j, f"причинность нарушена {f} {t}"
        folds.append({"t": t, "j": j, "info": info, "hash": hashes[j], "p": info[j][0], "dsr": info[j][1], "plateau": plateau(f, j, info)})
        json.dump({"fam": f, "test_month": t, "hash": hashes[j], "p_train": info[j][0], "dsr_train": info[j][1],
                   "axes": cells[f][j]["axes"]}, open(f"{outdir}/pick-{f}-{t:02d}.json", "w", encoding="utf-8"), ensure_ascii=False)
    res[f] = folds
print("ворота (б): причинность — автотест подмены пройден по всем фолдам/семьям")
nt_ = [n for n, _ in TIES[0::2]]   # вызовы идут парами (выбор, автотест)
print("тай-брейк §3: ничьих (число клеток на минимуме ключа >1) в выборах:", sum(1 for n in nt_ if n > 1), "из", len(nt_))

# ---- OOS ----
tdays = [i for t in TEST for i in range(*MON[t])]; pos = {d: k for k, d in enumerate(tdays)}; NT = len(tdays)
def oos_series(f, excl_coin=None, drop_days=()):
    """склеенные посуточные $ (выбранные, B1) на тестовых сутках; ряд None, если фолд без кандидатов"""
    a = np.zeros(NT); bq = np.zeros(NT); coin = {}; n_by_mo = {}
    for fo in res[f]:
        t = fo["t"]; mo = f"2026-{t:02d}"
        if fo.get("none"): return None
        keys = keymap[fo["hash"]]["keys"]
        nm = 0
        for i, u, sy in trades(keys, mo):
            nm += 1
            if sy != excl_coin: a[pos[i]] += u
            coin[sy] = coin.get(sy, 0) + u
        n_by_mo[t] = nm
        for i, u, sy in trades(b1keys, mo):
            if sy != excl_coin: bq[pos[i]] += u
            coin[sy] = coin.get(sy, 0) - u
    keep = np.ones(NT, bool)
    for d in drop_days: keep[d] = False
    return a[keep], bq[keep], coin, n_by_mo
def kpi(x):
    """В-120 «до перехая» месяца с нуля, суточное разрешение: (худший период максимум→новый максимум, хвост), сутки"""
    cum = peak = 0.0; tp = 0; per = [0]
    for i, v in enumerate(x):
        cum += v
        if cum > peak + 1e-9: per.append(i - tp); peak, tp = cum, i
    return [max(per), len(x) - tp]
def dsr_of(a, bq): return float(sr(a) - sr(bq))

out = {}; pvals = {}
idxc = boot_idx(NT, np.random.default_rng(SEED * 1000 + 1))
rngl = np.random.default_rng(SEED * 1000 + 2)
for f in FAM:
    o = oos_series(f)
    if o is None: out[f] = {"status": "не определена (фолд без кандидатов)"}; pvals[f] = 1.0; continue
    a, bq, coin, nmo = o
    d = dsr_of(a, bq); db = sr(a[idxc]) - sr(bq[idxc]); pB1 = pval(d, db)
    # жребий: по одной клетке на фолд равновероятно среди определённых на обучении
    cols = []
    for fo in res[f]:
        v = sorted(fo["info"]); ch = rngl.integers(0, len(v), size=B)
        t = fo["t"]; cols.append(X[f][np.array(v)[ch]][:, MON[t][0]:MON[t][1]])
    R = np.concatenate(cols, axis=1); srr = sr(R); sr_sel = float(sr(a))
    pL = (1 + np.sum(srr >= sr_sel)) / (B + 1); dc = sr_sel - float(srr.mean())
    pf = max(pB1, pL); pvals[f] = pf
    # по месяцам
    mon = {}
    for fo in res[f]:
        t = fo["t"]; mo = f"2026-{t:02d}"; lo, hi = MON[t]; k0, k1 = pos[lo], pos[hi - 1] + 1
        sa, sb_ = a[k0:k1], bq[k0:k1]
        v = sorted(fo["info"]); tot = X[f][v][:, lo:hi].sum(axis=1)
        mon[t] = {"hash": fo["hash"], "axes": cells[f][fo["j"]]["axes"], "p_train": round(fo["p"], 4), "dsr_train": round(fo["dsr"], 4),
                  "n_cand": len(v), "trades": nmo[t], "d_usd_vs_b1": round(float(sa.sum() - sb_.sum()), 2),
                  "d_usd_vs_median": round(float(sa.sum() - np.median(tot)), 2), "dsr_test": round(float(sr(sa) - sr(sb_)), 4),
                  "plateau": fo["plateau"], "kpi_sel": kpi(sa), "kpi_b1": kpi(sb_)}
        if len(v) > 2:   # Спирмен: ранг ΔSR обучения ↔ ранг $ на тесте
            rk = lambda z: np.argsort(np.argsort(z)).astype(float)
            tr = np.array([fo["info"][j][1] for j in v]); mon[t]["spearman"] = round(float(np.corrcoef(rk(tr), rk(tot))[0, 1]), 3)
    hs = [res[f][i]["hash"] for i in range(len(res[f]))]
    out[f] = {"dsr_oos": round(d, 4), "sr_sel": round(sr_sel, 4), "sr_b1": round(float(sr(bq)), 4), "p_b1": round(pB1, 4),
              "p_lot": round(float(pL), 4), "delta_c": round(dc, 4), "p_fam": round(pf, 4), "months": mon,
              "folds_pos": sum(1 for t in mon if mon[t]["d_usd_vs_b1"] > 0), "switches": sum(1 for i in range(1, 6) if hs[i] != hs[i - 1]),
              "usd_sel": round(float(a.sum()), 2), "usd_b1": round(float(bq.sum()), 2), "_coin": coin}
    # печать §5: Г-133-метрики, KPI склейки, DSR, PBO
    se_sr = float(sr(a[idxc]).std(ddof=1)); nsel = sum(nmo.values()); nb1 = sum(len(trades(b1keys, f"2026-{t:02d}")) for t in TEST)
    out[f]["z_paired"] = round(d / float(db.std(ddof=1)), 2); out[f]["sigma_paired"] = round(float(db.std(ddof=1)), 5)
    out[f]["trades_sel"] = nsel; out[f]["trades_b1"] = nb1; out[f]["d_usd_per_trade"] = round((float(a.sum()) - float(bq.sum())) / max(nsel, 1), 3)
    out[f]["kpi_worst_sel"] = max(mon[t]["kpi_sel"][0] if mon[t]["kpi_sel"][0] >= mon[t]["kpi_sel"][1] else mon[t]["kpi_sel"][1] for t in mon)
    out[f]["kpi_worst_b1"] = max(max(mon[t]["kpi_b1"]) for t in mon)
    out[f]["dsr_N_family"] = round(deflated_sharpe(sr_sel, se_sr, len(cells[f])), 4); out[f]["dsr_N_all195"] = round(deflated_sharpe(sr_sel, se_sr, 195), 4)
    out[f]["dsr_N_42"] = round(deflated_sharpe(sr_sel, se_sr, 42), 4); out[f]["sr_se_boot"] = round(se_sr, 4)
    mm = np.stack([X[f][:, MON[m][0]:MON[m][1]].sum(axis=1) for m in range(1, 10)])   # месяцы × клетки
    out[f]["pbo_months"] = pbo(mm, n_splits=8); out[f]["pbo_days"] = pbo(X[f].T, n_splits=10)
    out[f]["plateau_folds"] = [res[f][i]["plateau"] for i in range(len(res[f]))]
# Холм
order = sorted(FAM, key=lambda f: pvals[f]); reject = {}; stop = False
for r_, f in enumerate(order):
    thr = ALPHA / (len(FAM) - r_)
    ok = (not stop) and pvals[f] <= thr
    if not ok: stop = True
    reject[f] = (ok, thr)
# вердикт §7 + C2
for f in FAM:
    if "months" not in out[f]: out[f]["verdict"] = "не различимо (не определена)"; continue
    o = out[f]; mon = o["months"]
    a, bq, coin, nmo = oos_series(f); diff = a - bq
    aug, sep = mon[8]["dsr_test"], mon[9]["dsr_test"]
    # C2 для «держится»: без 2 суток с наибольшей положительной разностью и без монеты с наибольшим вкладом
    top2 = list(np.argsort(-diff)[:2]); cbest = max(coin, key=lambda c: coin[c])
    c2h = dsr_of(*oos_series(f, drop_days=top2)[:2]) > 0 and dsr_of(*oos_series(f, excl_coin=cbest)[:2]) > 0
    bot2 = list(np.argsort(diff)[:2]); cworst = min(coin, key=lambda c: coin[c])
    c2n = dsr_of(*oos_series(f, drop_days=bot2)[:2]) <= 0 and dsr_of(*oos_series(f, excl_coin=cworst)[:2]) <= 0
    o["c2_hold"] = bool(c2h); o["c2_notheld"] = bool(c2n); o["coin_best"] = cbest; o["coin_worst"] = cworst
    o["holm"] = {"rejected": reject[f][0], "thr": round(reject[f][1], 5)}
    if reject[f][0] and aug > 0 and sep > 0 and mon[8]["trades"] >= 20 and mon[9]["trades"] >= 20 and c2h:
        o["verdict"] = "ДЕРЖИТСЯ вперёд"
    elif o["dsr_oos"] <= 0 and o["delta_c"] <= 0 and aug <= 0 and sep <= 0 and c2n:
        o["verdict"] = "НЕ ДЕРЖИТСЯ"
    else: o["verdict"] = "не различимо"
    del o["_coin"]
# достижимость §6
se_b1 = float(sr(b1[tdays][idxc]).std(ddof=1)); nd = statistics.NormalDist()
reach = {"n_days": NT, "se_sr_b1_boot": round(se_b1, 4), "mde_alpha05": round(1.645 * se_b1, 4),
         "mde_holm9": round(nd.inv_cdf(1 - ALPHA / 9) * se_b1, 4), "power80_holm9": round((nd.inv_cdf(1 - ALPHA / 9) + 0.842) * se_b1, 4),
         "ref_effect_g93_u1K3": 0.0343, "note": "se по ряду B1 (бутстреп) — верхняя оценка; парный se выбранной клетки обычно меньше"}
json.dump({"reach": reach, "gates": {"letopis_checked": lchk, "letopis_bad": lbad, "letopis_cells_missing": lmiss, "coverage_bad": covbad,
                                       "ties": sum(1 for n in nt_ if n > 1), "selections": len(nt_)}, "families": out, "holm": {f: list(v) for f, v in reject.items()}, "gates_sum": {"sum_checked": chk, "sum_bad": bad}},
          open(f"{outdir}/walkforward-result.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1, default=str)
print("достижимость:", reach)
for f in FAM:
    o = out[f]
    if "months" not in o: print(f, o); continue
    print(f, "| ΔSR_oos", o["dsr_oos"], "p_B1", o["p_b1"], "p_жребий", o["p_lot"], "Δс", o["delta_c"], "p_сем", o["p_fam"],
          "Холм", o["holm"], "фолдов Δ$>0:", o["folds_pos"], "смен центра:", o["switches"], "→", o["verdict"],
          "| авг", o["months"][8]["dsr_test"], "сен", o["months"][9]["dsr_test"], "C2h", o["c2_hold"], "C2n", o["c2_notheld"],
          "| z", o["z_paired"], "Δ$/сд", o["d_usd_per_trade"], "KPI sel/b1", o["kpi_worst_sel"], o["kpi_worst_b1"], "DSR(N=сем/195/42)", o["dsr_N_family"], o["dsr_N_all195"], o["dsr_N_42"],
          "PBO мес/сут", o["pbo_months"]["pbo"], o["pbo_days"]["pbo"], "плато по фолдам", o["plateau_folds"])
