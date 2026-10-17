#!/usr/bin/env python3
"""TK-274 Г-03 (П-15 §5): Δ = SR(B1 на стенах) − SR(B1 на двойниках, среднее по 5 seed), по календарным суткам UTC.
Вход: каталог выхода tk274-g03.sh (<O>/run-*/out/<сутки>/{paired,random}<seed>/t-*/rounds.csv). Стены — режим paired
(те же подходы, что получили двойника), двойники — random; сутки без сделок = 0 (все сутки окна, как tk237-sieve.py).
usd сделки = net_bps · entry_px · qty / 1e4. Парный блочный бутстреп по суткам (циклический, блок ⌈n^{1/3}⌉), p односторонний
по центрированному Δ*. Окна: янв–сен, каждый месяц; C2 — без двух суток с наибольшей парной разностью и без монеты с наибольшим вкладом.
Сопоставимость: сделок двойников / сделок стен в [0,5; 2] (янв–сен и по месяцу; иначе контроль не читается).
  python tk274-g03-delta.py <g03-out> <out.csv> [--boot 4000]
"""
import csv, glob, math, os, sys, datetime, collections
import numpy as np

root, out = sys.argv[1:3]
B = int(sys.argv[sys.argv.index("--boot") + 1]) if "--boot" in sys.argv else 4000
SEEDS = (1, 2, 3, 4, 5)
d0 = datetime.date(2026, 1, 1); ND = (datetime.date(2026, 9, 30) - d0).days + 1
day_i = lambda s: (datetime.date.fromisoformat(s) - d0).days
mon_of = np.array([(d0 + datetime.timedelta(days=i)).month for i in range(ND)])

# day[mode][seed] -> float[ND]; coin[mode][seed][sym] -> float[ND]; ntr[mode][seed][month] -> int
day = {m: {s: np.zeros(ND) for s in SEEDS} for m in ("paired", "random")}
coin = {m: {s: collections.defaultdict(lambda: np.zeros(ND)) for s in SEEDS} for m in day}
ntr = {m: {s: np.zeros(10, int) for s in SEEDS} for m in day}
nfiles = 0
for f in glob.glob(f"{root}/run-*/out/*/*/t-*/rounds.csv"):
    parts = f.replace("\\", "/").split("/"); mode_seed = parts[-3]
    mode, seed = mode_seed[:-1], int(mode_seed[-1])
    if mode not in day: continue
    nfiles += 1
    with open(f, encoding="utf-8") as fh:
        for r in csv.DictReader(l for l in fh if not l.startswith("#")):
            i = day_i(r["day_utc"])
            if not 0 <= i < ND: continue
            usd = float(r["net_bps"]) * float(r["entry_px"]) * float(r["qty"]) / 1e4
            day[mode][seed][i] += usd; coin[mode][seed][r["symbol"]][i] += usd; ntr[mode][seed][mon_of[i]] += 1
print(f"файлов rounds.csv: {nfiles} (ждём {251 * 10})")
wl = np.mean([day["paired"][s] for s in SEEDS], axis=0); tw = np.mean([day["random"][s] for s in SEEDS], axis=0)
sr = lambda x: x.mean(axis=-1) / np.maximum(x.std(axis=-1, ddof=1), 1e-12)
rng = np.random.default_rng(15)

def boot(a, b):
    n = len(a); blk = math.ceil(n ** (1 / 3)); nb = math.ceil(n / blk)
    idx = ((rng.integers(0, n, size=(B, nb))[:, :, None] + np.arange(blk)[None, None, :]) % n).reshape(B, -1)[:, :n]
    db = sr(a[idx]) - sr(b[idx]); d = sr(a) - sr(b)
    return d, float(db.std(ddof=1)), (1 + np.sum((db - d) >= d)) / (B + 1) if d > 0 else 1.0

windows = [("янв–сен", np.ones(ND, bool))] + [(f"2026-{m:02d}", mon_of == m) for m in range(1, 10)]
rows = []; res = {}
for name, msk in windows:
    a, b = wl[msk], tw[msk]
    d, se, p = boot(a, b)
    mons = sorted(set(mon_of[msk]))
    nw = sum(np.mean([ntr["paired"][s][m] for s in SEEDS]) for m in mons); nt = sum(np.mean([ntr["random"][s][m] for s in SEEDS]) for m in mons)
    ratio = nt / nw if nw else float("nan")
    per_seed = [float(sr(day["paired"][s][msk]) - sr(day["random"][s][msk])) for s in SEEDS]
    res[name] = dict(d=float(d), p=float(p))
    rows.append([name, round(nw, 1), round(nt, 1), round(ratio, 3) if nw else "", "да" if nw and 0.5 <= ratio <= 2 else "нет",
                 round(float(sr(a)), 4), round(float(sr(b)), 4), round(float(d), 4), round(se, 4), round(float(p), 4),
                 round(min(per_seed), 4), round(max(per_seed), 4), round(float(a.sum()), 2), round(float(b.sum()), 2),
                 int((a != 0).sum()), int((b != 0).sum())])
# C2: без двух суток с наибольшей парной разностью и без монеты с наибольшим вкладом (окно янв–сен)
diff = wl - tw; drop = np.argsort(-diff)[:2]; keep = np.ones(ND, bool); keep[drop] = False
syms = set().union(*[set(coin[m][s].keys()) for m in coin for s in SEEDS])
cs = {x: float(np.mean([coin["paired"][s][x] for s in SEEDS], axis=0).sum() - np.mean([coin["random"][s][x] for s in SEEDS], axis=0).sum())
      for x in syms}
top = max(cs, key=cs.get)
minus_coin = lambda mode: np.mean([day[mode][s] - coin[mode][s][top] for s in SEEDS], axis=0)
c2d = float(sr(wl[keep]) - sr(tw[keep])); c2c = float(sr(minus_coin("paired")) - sr(minus_coin("random")))
c2 = c2d > 0 and c2c > 0
hd = ["window", "trades_walls", "trades_twins", "ratio", "comparable", "SR_walls", "SR_twins", "dSR", "se", "p", "dSR_seed_min",
      "dSR_seed_max", "usd_walls", "usd_twins", "days_walls", "days_twins"]
with open(out, "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f); w.writerow(hd); w.writerows(rows)
S, A, N = res["янв–сен"], res["2026-08"], res["2026-09"]
comp = rows[0][4] == "да"
verdict = ("контроль не сопоставим" if not comp else
           "стена — причина" if S["d"] > 0 and S["p"] <= 0.05 and A["d"] > 0 and N["d"] > 0 and c2 else
           "не от стены" if S["d"] <= 0 and A["d"] <= 0 and N["d"] <= 0 else "не различимо")
print(f"C2: без 2 суток {sorted(str(d0 + datetime.timedelta(days=int(i))) for i in drop)} dSR {c2d:.4f}; без монеты {top} dSR {c2c:.4f}; C2 {'ок' if c2 else 'нет'}")
print(f"вердикт B1 (П-15 §5): {verdict}; янв–сен dSR {S['d']:.4f} p {S['p']:.4f}; авг {A['d']:.4f}; сен {N['d']:.4f}")
for r in rows: print(r)
with open(out + ".verdict.txt", "w", encoding="utf-8") as f:
    f.write(f"{verdict}\nC2 {'ок' if c2 else 'нет'} (без 2 суток {c2d:.4f}; без монеты {top} {c2c:.4f})\n")
