#!/usr/bin/env python3
"""TK-373 / P-25: ОБРАТНЫЙ тест «Bybit ведёт DEX». Событие = резкий ход середины Bybit за W с (|ход| >= q95 своей монеты по 15.10, порог
фиксирован до окна счёта 16–21.10; не чаще раза в 60 с). Сторона s = знак хода. После t0=ts+1 на горизонтах HP (10/60/300 с) меряем на DEX:
imb = s*(buy-sell)/(buy+sell) USD пулов монеты (поток по стороне хода), dpx = s*ход цены последнего свопа (бп), by = s*ход Bybit дальше (бп);
до события (W с до t0): imb_pre. Контроль — те же величины в моменты t±3600 с (та же сторона s), net = M - среднее контролей.
env VERS=v2|v3 — только пулы этой версии. usage: tk373-rev.py <usd> <sec> <universe.csv> <outdir> [P=15]   (движок tk369-run2.py)"""
import sys, os, json, bisect, gzip, importlib.util
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("r2", os.path.join(HERE, "tk369-run2.py"))
r2 = importlib.util.module_from_spec(spec); sys.modules["r2"] = r2; spec.loader.exec_module(r2)
VERS = os.environ.get("VERS", "")
VERMAP = {}
if VERS:
    for _v in json.load(open(os.environ.get("POOLSJSON", "/data/tk0373/pools-all.json"))).values():
        if _v:
            _l = "".join(_v.get("labels") or []).replace("v", "v")
            VERMAP[_v["pool"].lower()] = "v3" if "v3" in _l else ("v2" if "v2" in _l else "")
WS = (10, 60); HP = (10, 60, 300); SHIFT = 3600; COOL = 60

class Dex:
    def __init__(s, sw):
        s.t = [x[0] for x in sw]
        s.pxs = [x[3] for x in sw]
        s.cs = [0.0]; s.ca = [0.0]
        for ts, sd, amt, pu in sw:
            u = amt * pu
            s.cs.append(s.cs[-1] + sd * u); s.ca.append(s.ca[-1] + u)
    def imb(s, a, b):             # свопы с a < ts <= b
        i, j = bisect.bisect_right(s.t, a), bisect.bisect_right(s.t, b)
        tot = s.ca[j] - s.ca[i]
        return (s.cs[j] - s.cs[i]) / tot if tot > 0 else None
    def px(s, t):
        i = bisect.bisect_right(s.t, t) - 1
        return s.pxs[i] if i >= 0 and s.pxs[i] > 0 else None

def stats(fut, dex, t0, s, W):
    p0 = r2.pm(fut, t0)
    if p0 is None: return None
    out = []
    d0 = dex.px(t0)
    v = dex.imb(t0 - W, t0); out.append(s * v if v is not None else None)          # imb_pre
    for h in HP:
        v = dex.imb(t0, t0 + h); out.append(s * v if v is not None else None)       # imb_post
        d1 = dex.px(t0 + h)
        out.append(s * (d1 / d0 - 1) * 1e4 if d0 and d1 else None)                  # dpx
        p1 = r2.pm(fut, t0 + h)
        out.append(s * (p1 - p0) / p0 * 1e4 if p1 else None)                        # by
    return out

def work(job):
    key, coin_dirs, sym = job
    fut = r2.load_mid(sym, "fut")
    if fut is None: return key, {"err": "no_fut_mid"}
    pools = r2.read_swaps(coin_dirs)
    if VERS:                                  # TK-373: только пулы заданной версии (v2/v3) по меткам Dexscreener (pools-all.json)
        pools = {k: v for k, v in pools.items() if VERMAP.get(k[1].lower()) == VERS}
    allsw = sorted((x[0], x[1], x[2], x[3]) for sw in pools.values() for x in sw if x[3] > 0)
    if len(allsw) < 50: return key, {"err": "few_swaps", "n": len(allsw)}
    dex = Dex(allsw)
    rows = []
    for W in WS:
        base = []
        for t in range(r2.T15 + W + 10, r2.T15 + r2.DAY, 10):               # 15.10 — только порог
            a, b = r2.pm(fut, t), r2.pm(fut, t - W)
            if a and b: base.append(abs((a - b) / b * 1e4))
        if len(base) < 500: continue
        base.sort(); thr = r2.qthr(base, .95)
        if thr <= 0: continue
        last = -10**9
        for ts in range(r2.SCORE0, r2.SCORE1 - 4000):
            if ts - last < COOL: continue
            a, b = r2.pm(fut, ts + 1), r2.pm(fut, ts + 1 - W)
            if not a or not b: continue
            r = (a - b) / b * 1e4
            if abs(r) < thr: continue
            s = 1 if r > 0 else -1
            last = ts
            m = stats(fut, dex, ts + 1, s, W)
            if m is None: continue
            cs = [c for c in (stats(fut, dex, ts + 1 - SHIFT, s, W), stats(fut, dex, ts + 1 + SHIFT, s, W)) if c]
            net = []
            for i, x in enumerate(m):
                cv = [c[i] for c in cs if c[i] is not None]
                net.append(f"{x:.4f}" if x is not None else "")
                net.append(f"{x - sum(cv) / len(cv):.4f}" if (x is not None and cv) else "")
            rows.append("\t".join([f"R{W}", str((ts - r2.T15) // r2.DAY + 15), str(s), f"{abs(r) / thr:.3f}"] + net))
    os.makedirs(f"{r2.OUT}/coin", exist_ok=True)
    with gzip.open(f"{r2.OUT}/coin/{key}.tsv.gz", "wt") as fh:
        fh.write("\n".join(rows) + "\n")
    return key, {"ok": 1, "n": len(rows)}

def main():
    import csv
    TICK = {r["symbol"]: 1 for r in csv.DictReader(open(os.environ.get("TICKCSV", "/srv/alpha-sda/tk366w/instruments.csv"), encoding="utf-8"))}
    uni = {}
    for r in csv.DictReader(open(r2.UNIV, encoding="utf-8")): uni.setdefault(r["base"], []).append(r)
    coins = {}
    for chain in ("ethereum", "bsc", "base"):
        for d in sorted(os.listdir(f"{r2.USD}/{chain}")):
            base, _, suf = d.partition("-")
            rows = uni.get(base)
            if not rows: continue
            row = rows[0]; sym = row["symbol"]
            if not os.path.exists(f"{r2.MID}/fut/2026-10-16/{sym}-2026-10-16.csv.gz") or sym not in TICK: continue
            if row.get("cg_wrapped") == "1": continue
            if suf and not row["token"].lower().endswith(suf.lower()): continue
            coins.setdefault(base, {"sym": sym, "dirs": []})["dirs"].append((chain, d))
    jobs = [(b, v["dirs"], v["sym"]) for b, v in sorted(coins.items())]
    print(f"coins {len(jobs)}", flush=True)
    with Pool(r2.P) as pool:
        for i, (key, st) in enumerate(pool.imap_unordered(work, jobs), 1): print(i, key, st, flush=True)
    open(f"{r2.OUT}/DONE.json", "w").write(json.dumps({"coins": len(jobs)}))

if __name__ == "__main__":
    main()
