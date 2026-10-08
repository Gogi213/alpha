#!/usr/bin/env python3
"""TK-063: П-12 §9 п.3 С2 «без монеты с наибольшим вкладом» и §6 п.1 пик одновременных позиций — для клеток g93 (прошли п.1) и g92/e119 (опровергнуты).
Вход: data/tk063/coin-rows.csv (rounds vn-b), data/p12r2/closes-vn-cap{0,3}-<мес>.json (closes, $_норм). Сопоставление close->строка: exit_ms + $ (округл. 4).
Монета с наибольшим вкладом = max по |ΔSR-вкладу|: монета, чьё удаление (из клетки и из B1) сильнее всего уменьшает ΔSR. ΔSR и нижняя 95% — тем же блочным бутстрепом (seed 64), что p12-sharpe.py.
Режим: free (cap0) | B2 (cap3). Печать + docs/findings/p12-coin-c2-<режим>-2026-10-08.csv."""
import csv, json, sys
from collections import defaultdict
import numpy as np

MODE = sys.argv[1] if len(sys.argv) > 1 else "free"
CAP = "0" if MODE == "free" else "3"
src = open("tools/compute/p12-sharpe.py", encoding="utf-8").read().rsplit("\nmain()", 1)[0]
sys.argv = ["x", MODE]
exec(compile(src, "p12-sharpe.py", "exec"))   # load, pack, sr, block_idx, B, POOL
ns = load("tools/compute/p12-r2-analyze.py", MODE)
P = pack(ns, False)
CELLS, day_of, di = ns["CELLS"], ns["day_of"], ns["di"]
PRE = "t-bid-btc4h-q1@ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
SUF = {"B1": ""}
SUF.update({c: v[2] for c, v in CELLS.items() if v[0] in ("g93", "g92", "e119")})
ROWS = defaultdict(list)   # (форма-суффикс, мес) -> [(exit_ms, $, символ, t0)]
for r in csv.DictReader(open("data/tk063/coin-rows.csv", encoding="utf-8")):
    suf = "" if r["form"] == "B1" else r["form"]
    usd = float(r["qty"]) * float(r["entry_vwap"]) * float(r["net_bps"]) / 1e4
    ROWS[(suf, r["month"])].append((int(r["exit_ms"]), usd, r["symbol"], int(r["t0_ms"])))
months = sorted(POOL)
cal0 = next(iter(P.values()))["cal"]
keep = np.array([d[:7] in POOL for d in cal0]); cal = [d for d, k in zip(cal0, keep) if k]; n = len(cal)
mon = np.array([d[:7] for d in cal]); dpos = {d: i for i, d in enumerate(cal)}


def matched(suf):
    """-> [(день, $_closes, символ, t0, exit)] по closes; по месяцам фев–сен."""
    out = []
    for m in months:
        j = json.load(open(f"data/p12r2/closes-vn-cap{CAP}-{m}.json"))
        lst = [(int(a), float(b)) for per in j[PRE + suf].values() for caps in per.values() for a, b in caps]
        pool = defaultdict(list)
        for ex, usd, sym, t0 in ROWS[(suf, m)]:
            pool[ex].append((usd, sym, t0))
        for ms, v in lst:
            c = pool.get(ms, [])
            hit = min(range(len(c)), key=lambda i: abs(c[i][0] - v)) if c else None
            if hit is None or abs(c[hit][0] - v) > 6e-4:
                raise SystemExit(f"нет пары {suf} {m} {ms} {v}")
            usd, sym, t0 = c.pop(hit)
            out.append((day_of(ms), v, sym, t0, ms))
    return out


idx = block_idx(np.random.default_rng(64), n)
B1m = matched("")


def daily(rows, drop=None):
    a = np.zeros(n)
    for d, v, sym, _t0, _ex in rows:
        if drop and sym == drop:
            continue
        if d in dpos:
            a[dpos[d]] += v
    return a


def peak(rows):
    ev = sorted([(t0, 1) for _d, _v, _s, t0, _e in rows] + [(e, -1) for _d, _v, _s, _t, e in rows], key=lambda x: (x[0], x[1]))
    cur = best = 0
    for _t, k in ev:
        cur += k; best = max(best, cur)
    pm = {}
    for m in months:
        r = [x for x in rows if x[0][:7] == m]
        e2 = sorted([(x[3], 1) for x in r] + [(x[4], -1) for x in r], key=lambda x: (x[0], x[1]))
        c = b = 0
        for _t, k in e2:
            c += k; b = max(b, c)
        pm[m] = b
    return best, pm


out = [["mode", "cell", "dm", "d_sr", "top_coin", "d_sr_without", "ci_lo_without", "c2_coin", "peak", "peak_b1", "peak_gt_b1_months", "trades_over_1.25_months"]]
pb1, pmb1 = peak(B1m)
print(f"{MODE}: B1 пик позиций {pb1}; по месяцам {pmb1}; сделок по месяцам", {m: sum(1 for x in B1m if x[0][:7] == m) for m in months})
base_dm = P["B1"]["dfn"] & POOL
for c, suf in SUF.items():
    if c == "B1":
        continue
    rows = matched(suf)
    dm = P[c]["dfn"] & base_dm
    mk = np.isin(mon, sorted(dm)).astype(float)
    ms = mk[idx]
    x, y = daily(rows), daily(B1m)
    full = float(sr(x, mk) - sr(y, mk))
    coins = sorted({r[2] for r in rows} | {r[2] for r in B1m})
    best = None
    for coin in coins:
        d = float(sr(daily(rows, coin), mk) - sr(daily(B1m, coin), mk))
        if best is None or d < best[1]:
            best = (coin, d)
    coin = best[0]
    xs, ys = daily(rows, coin)[idx], daily(B1m, coin)[idx]
    dd = sr(xs, ms) - sr(ys, ms)
    lo = float(np.percentile(dd, 2.5))
    pk, pm = peak(rows)
    over = [m for m in months if pm[m] > pmb1[m]]
    cnt = [m for m in months if sum(1 for r in rows if r[0][:7] == m) > 1.25 * sum(1 for r in B1m if r[0][:7] == m)]
    c2 = best[1] >= 0 and lo >= 0
    print(f"{c}: ΔSR {full:+.4f}; без {coin}: {best[1]:+.4f} [нижн. {lo:+.4f}] С2-монета {'да' if c2 else 'НЕТ'}; пик {pk} (B1 {pb1}); мес. пик>B1: {over}; сделок>1,25×: {cnt}")
    out.append([MODE, c, len(dm), round(full, 4), coin, round(best[1], 4), round(lo, 4), int(c2), pk, pb1, ";".join(over), ";".join(cnt)])
with open(f"docs/findings/p12-coin-c2-{MODE}-2026-10-08.csv", "w", encoding="utf-8", newline="") as fh:
    csv.writer(fh, lineterminator="\n").writerows(out)
