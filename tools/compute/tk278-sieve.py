#!/usr/bin/env python3
"""TK-278 п.4: сито Q-1 П-14 F1 заново по всем 50 гипотезам (1146 хешей) — клетка vs B1, половина А, янв–сен.
Метод и пороги — как tk237-sieve.py (ΔSR по суткам UTC, парный циклический блочный бутстреп, блок ⌈n^{1/3}⌉, B=4000, seed 14, годна: p ≤ 0,20,
сделок ≥ 20, суток со сделками ≥ 10). Новое: closes с монетой берутся по источникам (TK-278 п.1–2): R1 (r1x/r1col/r1old, ключ = хеш или имя клетки,
база B1 — 'B1' того же дерева), Э/К/F1/R2-B (ключ набор@форма, база B1 деревьев b1-b TK-191 с вырезом монето-суток как в TK-237),
М (Г-87/92/93/95) — closes с m в qty (нотионал), база B1 деревьев b1-b. Нет ключа в месяце = 0 сделок (psim пишет только варианты со сделками).
  python tk278-sieve.py <keymap.json> <coverage.csv> <cellhash-map.csv> <out.csv> [--boot 4000]"""
import csv, hashlib, json, math, os, sys, datetime, collections
import numpy as np

kmap_f, cov_f, map_f, out = sys.argv[1:5]
B = int(sys.argv[sys.argv.index("--boot") + 1]) if "--boot" in sys.argv else 4000
B1 = "t-bid-btc4h-q1@ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
EQ = "ladder3x0..0.0409sw10-pct2-tr1x1-14400-ttl1800"
M_HYP = {"Г-87", "Г-92", "Г-93", "Г-95"}
isA = lambda s: int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0
d0 = datetime.date(2026, 1, 1); ND = (datetime.date(2026, 9, 30) - d0).days + 1
UTC = lambda ms: datetime.datetime.fromtimestamp(ms / 1000, datetime.timezone.utc).date()
MON = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep"]
P = "/data/tk0278"
keymap = json.load(open(kmap_f, encoding="utf-8"))
cells = list(csv.DictReader(open(cov_f, encoding="utf-8")))
oldname = {}   # имя клетки R1 (g04-k20) -> канонический хеш
for r in csv.reader(open(map_f, encoding="utf-8"), delimiter=";"):
    if len(r) > 3 and r[2].startswith("имя клетки R1"): oldname[r[1]] = r[3]
R1 = {"r1x": "psim", "r1col": "psim", "r1old": "psim"}
r1_key = {}   # хеш -> {источник: ключ}
for h, nm in oldname.items(): r1_key.setdefault(h, {})["r1old"] = nm
sys.stderr.write(f"keymap {len(keymap)}, клеток {len(cells)}, имён R1 {len(oldname)}\n")


class Acc:
    def __init__(s): s.day = np.zeros(ND); s.has = np.zeros(ND, bool); s.n = 0; s.bm = {}
    def add(s, mo, rows, excl):
        nm = sm = 0
        for ms, usd, sym in rows:
            if not isA(sym) or (excl and (sym, UTC(ms)) in excl): continue
            i = (UTC(ms) - d0).days
            if 0 <= i < ND: s.day[i] += usd; s.has[i] = True; s.n += 1; nm += 1; sm += usd
        s.bm[mo] = (nm, sm)


def load(path):
    return json.load(open(path, encoding="utf-8")) if os.path.exists(path) else {}


def rows_of(d, key, mo):
    return d.get(key, {}).get(mo, {}).get("0", [])


acc = {}                                  # хеш -> Acc
base = {"b1": Acc(), "r1x": Acc(), "r1col": Acc(), "r1old": Acc()}   # база: b1-деревья / 'B1' дерева R1
src_of = {}                               # хеш -> источник (для отчёта)
cmap = {r["hash"]: r for r in cells}
for r in cells:
    h = r["hash"]
    if r["hyp"] in M_HYP: src_of[h] = "M"
    elif h in r1_key or (h in keymap and not keymap[h]["keys"]): src_of[h] = "R1?"
    else: src_of[h] = "F1"
for m in range(1, 10):
    mo = f"2026-{m:02d}"; mm = f"{m:02d}"
    b1d = load(f"/data/p12r3a/closes-sym-b1-cap0-{mo}.json")
    f1 = load(f"/data/tk0196/psim/closes-sym-f1-cap0-{mo}.json")
    for k, v in load(f"/data/p12r3a/closes-sym-r2b-cap0-{mo}.json").items(): f1.setdefault(k, v)
    for s in ("e", "k"):
        for k, v in load(f"{P}/psim/closes-sym-{s}-cap0-{mm}.json").items(): f1.setdefault(k, v)
    mm_d = {}
    for fn in (f"{P}/expo/closes-sym-m-cap0-{mo}.json", f"{P}/expo-old/closes-sym-A-cap0-{mo}.json",
               f"{P}/expo-old/closes-sym-B3-cap0-{mo}.json", f"{P}/expo-old/closes-sym-B45-cap0-{mo}.json"):
        for k, v in load(fn).items(): mm_d.setdefault(k, v)
    r1d = {s: load(f"{P}/psim/closes-sym-{s}-cap0-{mm}.json") for s in R1}
    cd = lambda c: {(sym, UTC(ms)) for ms, u, sym in c}
    eqk = next((k for k in f1 if k.startswith("t-bid-btc4h-q1@") and k.endswith(EQ)), None)
    excl = (cd(rows_of(f1, eqk, mo)) - cd(rows_of(b1d, B1, mo))) if eqk else set()
    base["b1"].add(mo, rows_of(b1d, B1, mo), excl)
    for s in R1: base[s].add(mo, rows_of(r1d[s], "B1", mo), None)
    for r in cells:
        h = r["hash"]; hyp = r["hyp"]; a = acc.setdefault(h, Acc())
        if hyp in M_HYP:
            key = keymap.get(h, {}).get("keys", {}).get(mo)
            if key: a.add(mo, rows_of(mm_d, key, mo), excl)
            continue
        got = False
        for s in R1:   # R1: ключ = хеш (r1x/r1col) или имя клетки (r1old)
            key = r1_key.get(h, {}).get(s, h)
            if key in r1d[s]: a.add(mo, rows_of(r1d[s], key, mo), None); src_of[h] = s; got = True; break
        if got: continue
        key = keymap.get(h, {}).get("keys", {}).get(mo)
        if key and key in f1: a.add(mo, rows_of(f1, key, mo), excl); src_of[h] = "F1"
        elif key: a.add(mo, [], excl)   # ключ есть, сделок нет
    sys.stderr.write(f"{mo} загружен\n")

sr = lambda x: x.mean(axis=-1) / np.maximum(x.std(axis=-1, ddof=1), 1e-12)
blk = math.ceil(ND ** (1 / 3)); nb = math.ceil(ND / blk)
starts = np.random.default_rng(14).integers(0, ND, size=(B, nb))
idx = ((starts[:, :, None] + np.arange(blk)[None, None, :]) % ND).reshape(B, -1)[:, :ND]
bs = {k: (v.day, sr(v.day), sr(v.day[idx])) for k, v in base.items()}
for k, v in base.items(): sys.stderr.write(f"база {k}: сделок {v.n}, суток {int(v.has.sum())}, SR {bs[k][1]:.4f}\n")
rows = []; se_all = []
for r in cells:
    h = r["hash"]; hyp = r["hyp"]; a = acc[h]; s = src_of[h]
    bk = s if s in ("r1x", "r1col", "r1old") else "b1"
    if hyp in M_HYP: need = h in keymap and len(keymap[h]["keys"]) == 9
    elif s in ("r1x", "r1col", "r1old"): need = True
    else: need = h in keymap and len(keymap[h]["keys"]) == 9
    if not need or (a.n == 0 and s == "R1?"):
        rows.append([hyp, h, r["axes"], s, a.n, int(a.has.sum()), "", "", "", "не определена: нет данных (клетка не посчитана / нет кодировки)"]); continue
    dsr = sr(a.day) - bs[bk][1]; db = sr(a.day[idx]) - bs[bk][2]
    se = db.std(ddof=1); se_all.append(se)
    p = (1 + np.sum((db - dsr) >= dsr)) / (B + 1) if dsr > 0 else 1.0
    ok = a.n >= 20 and int(a.has.sum()) >= 10
    st = "не определена: сделок<20 или суток<10" if not ok else "годна" if p <= 0.20 else "не годна"
    rows.append([hyp, h, r["axes"], s, a.n, int(a.has.sum()), round(float(dsr), 4), round(float(p), 4), round(float(se), 4), st])
with open(out, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(["hyp", "hash", "axes", "source", "trades_A", "days_A", "dSR", "p", "se", "status"]); w.writerows(rows)
print("медиана SE", round(float(np.median(se_all)), 4))
c = collections.Counter(r[-1].split(":")[0] for r in rows); print(dict(c))
per = collections.defaultdict(collections.Counter)
for r in rows: per[r[0]][r[-1].split(":")[0] + (":" + r[-1].split(":")[1].strip()[:12] if r[-1].startswith("не опр") else "")] += 1
for h, v in sorted(per.items()): print(h, dict(v))
