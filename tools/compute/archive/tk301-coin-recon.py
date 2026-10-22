#!/usr/bin/env python3
"""TK-301 (П-20): B1 стены (Г-03 paired) против nowall-двойников по монетам. Вход: каталоги g03, g03nw (распакованные rounds.tgz).
  python tk301-coin-recon.py <g03> <g03nw> <out.csv> [--boot 4000]"""
import csv, glob, math, sys, datetime, collections
import numpy as np
root, troot, out = sys.argv[1:4]
B = int(sys.argv[sys.argv.index("--boot") + 1]) if "--boot" in sys.argv else 4000
SEEDS = (1, 2, 3, 4, 5)
d0 = datetime.date(2026, 1, 1); ND = (datetime.date(2026, 9, 30) - d0).days + 1
mon = np.array([(d0 + datetime.timedelta(days=i)).month for i in range(ND)])
usd = {k: collections.defaultdict(lambda: np.zeros(ND)) for k in ("w", "t")}
ntr = {k: collections.defaultdict(lambda: np.zeros(10)) for k in ("w", "t")}
for src, name, key in ((root, "paired", "w"), (troot, "nowall", "t")):
    for f in glob.glob(f"{src}/run-*/out/*/*/t-*/rounds.csv"):
        ms = f.replace("\\", "/").split("/")[-3]
        if ms[:-1] != name: continue
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(l for l in fh if not l.startswith("#")):
                i = (datetime.date.fromisoformat(r["day_utc"]) - d0).days
                if not 0 <= i < ND: continue
                usd[key][r["symbol"]][i] += float(r["net_bps"]) * float(r["entry_px"]) * float(r["qty"]) / 1e4 / len(SEEDS)
                ntr[key][r["symbol"]][mon[i]] += 1 / len(SEEDS)
syms = sorted(set(usd["w"]) | set(usd["t"]))
sr = lambda x: x.mean(axis=-1) / np.maximum(x.std(axis=-1, ddof=1), 1e-12)
rng = np.random.default_rng(15)
def boot(d):
    n = len(d); blk = math.ceil(n ** (1 / 3)); nb = math.ceil(n / blk)
    idx = ((rng.integers(0, n, size=(B, nb))[:, :, None] + np.arange(blk)[None, None, :]) % n).reshape(B, -1)[:, :n]
    m = d.mean(); mb = d[idx].mean(axis=1)
    return (1 + np.sum((mb - m) >= m)) / (B + 1) if m > 0 else 1.0
ok = lambda nw, nt: nw > 0 and 0.5 <= nt / nw <= 2
rows = []
for s in syms:
    w, t = usd["w"][s], usd["t"][s]; d = w - t
    nw, nt = ntr["w"][s].sum(), ntr["t"][s].sum()
    rd = []  # читаемые месяцы
    for m in range(1, 10):
        a, b = ntr["w"][s][m], ntr["t"][s][m]
        if a >= 5 and ok(a, b): rd.append(float(d[mon == m].sum()))
    k = len(rd); kpos = sum(x > 0 for x in rd)
    testable = nw >= 30 and ok(nw, nt)
    p = boot(d) if testable else float("nan")
    drop = np.argsort(-d)[:2]; keep = np.ones(ND, bool); keep[drop] = False
    rows.append(dict(symbol=s, trades_w=round(nw, 1), trades_t=round(nt, 1), ratio=round(nt / nw, 3) if nw else "", testable=testable,
                     D=round(float(d.sum()), 2), usd_w=round(float(w.sum()), 2), usd_t=round(float(t.sum()), 2),
                     dSR=round(float(sr(w) - sr(t)), 4), p=round(float(p), 4), months_read=k, months_pos=kpos,
                     D_no2=round(float(d[keep].sum()), 2), days_w=int((w != 0).sum()),
                     **{f"D_m{m:02d}": (round(float(d[mon == m].sum()), 2) if ntr["w"][s][m] or ntr["t"][s][m] else "") for m in range(1, 10)},
                     **{f"nw_m{m:02d}": round(float(ntr["w"][s][m]), 1) for m in range(1, 10)}))
m_t = sum(r["testable"] for r in rows); alpha = 0.05 / max(m_t, 1)
for r in rows:
    if not r["testable"]: r["verdict"] = "мало сделок"
    elif r["months_read"] < 3: r["verdict"] = "мало месяцев"
    elif r["months_pos"] >= math.ceil(2 * r["months_read"] / 3) and r["p"] <= alpha and r["D"] > 0 and r["D_no2"] > 0: r["verdict"] = "устойчива"
    else: r["verdict"] = "не устойчива"
def binom_tail(k, n): return sum(math.comb(n, j) for j in range(math.ceil(2 * n / 3), n + 1)) / 2 ** n if n else 0.0
exp_sign = sum(binom_tail(r["months_read"], r["months_read"]) for r in rows if r["testable"] and r["months_read"] >= 3)
rows.sort(key=lambda r: -r["trades_w"])
with open(out, "w", encoding="utf-8", newline="") as f:
    wr = csv.DictWriter(f, fieldnames=list(rows[0].keys()), extrasaction="ignore"); wr.writeheader(); wr.writerows(rows)
print(f"монет {len(rows)}, проверяемых m={m_t}, порог p {alpha:.4f}; ожидается случайно: с поправкой 0.05, без {0.05 * m_t:.2f}, по знакам {exp_sign:.3f}")
for r in rows: print({k: r[k] for k in ("symbol", "trades_w", "trades_t", "ratio", "D", "dSR", "p", "months_read", "months_pos", "D_no2", "verdict")})
