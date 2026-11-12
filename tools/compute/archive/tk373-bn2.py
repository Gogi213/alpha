#!/usr/bin/env python3
"""TK-373 / P-25: Binance<->Bybit/DEX, три режима (env MODE):
 rev  — ОБРАТНЫЙ тест: событие = ход середины Bybit за W∈{10,60} с >= q95 своей монеты по 16.10 -> ход Binance (последняя сделка) после t0.
 b2b  — I038–I041: ход Binance (последняя сделка) за W∈{1,5,30,300} с >= q99 по 16.10 -> Bybit после.
 flow — поток Binance (aggTrades, aggressor): |buy-sell USD| за W∈{10,60} с >= q95 своей монеты по 16.10, сторона = знак потока -> Bybit/Binance/DEX после.
Не чаще раза в 60 с, t0=ts+1, счёт 17.10–21.10, контроль t±3600 с, net = M - среднее контролей (как tk373-bn.py). Столбцы после hid,day,s,strength — пары (M,net):
 by10,by60,by300, bn10,bn60,bn300, bypre, bnpre, dpx10,dpx60,dpx300, imb60.   usage: tk373-bn2.py <usd> <sec> <universe.csv> <outdir> [P=15]
 env BINSEC=/data/tk0373/binsec, BINFLOW=/data/tk0373/binflow."""
import sys, os, json, gzip, csv, importlib.util
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__))
def _load(name, fn):
    sp = importlib.util.spec_from_file_location(name, os.path.join(HERE, fn))
    m = importlib.util.module_from_spec(sp); sys.modules[name] = m; sp.loader.exec_module(m); return m
r2 = _load("r2", "tk369-run2.py"); rev = _load("rev", "tk373-rev.py"); bn = _load("bn", "tk373-bn.py")
MODE = os.environ.get("MODE", "rev"); FLOW = os.environ.get("BINFLOW", "/data/tk0373/binflow")
HP = (10, 60, 300); SHIFT = 3600; COOL = 60; START = bn.START; SC0 = bn.SC0
CFG = {"rev": ((10, 60), .95, "REV"), "b2b": ((1, 5, 30, 300), .99, "B"), "flow": ((10, 60), .95, "FL")}[MODE]

def load_flow(sym):
    cum = [0.0] * (r2.NSEC + 1); have = 0
    for d in range(15, 22):
        f = f"{FLOW}/{sym}/2026-10-{d}.csv"
        if not os.path.exists(f): continue
        have += 1
        for ln in open(f):
            a = ln.split(",")
            if len(a) < 3: continue
            i = int(a[0]) - r2.T15
            if 0 <= i < r2.NSEC: cum[i + 1] += float(a[1] or 0) - float(a[2] or 0)
    if not have: return None
    for i in range(r2.NSEC): cum[i + 1] += cum[i]
    return cum

def sig(fut, b, fl, t, W):                     # сигнал на t0=t за окно [t-W,t): ход в бп (rev: Bybit, b2b: Binance) или поток USD (flow)
    if MODE == "rev":
        a, c = r2.pm(fut, t), r2.pm(fut, t - W)
        return (a - c) / c * 1e4 if a and c else None
    if MODE == "b2b":
        a, c = bn.bp(b, t), bn.bp(b, t - W)
        return (a - c) / c * 1e4 if a and c else None
    i, j = t - W - r2.T15, t - r2.T15
    return fl[j] - fl[i] if fl and i >= 0 and j <= r2.NSEC else None

def stats(fut, b, dex, t0, s, W):
    p0 = r2.pm(fut, t0); b0 = bn.bp(b, t0)
    if p0 is None or b0 is None: return None
    out = []
    for h in HP:
        p1 = r2.pm(fut, t0 + h); out.append(s * (p1 - p0) / p0 * 1e4 if p1 else None)
    for h in HP:
        b1 = bn.bp(b, t0 + h); out.append(s * (b1 - b0) / b0 * 1e4 if b1 else None)
    pb = r2.pm(fut, t0 - W); out.append(s * (p0 - pb) / pb * 1e4 if pb else None)
    bb = bn.bp(b, t0 - W); out.append(s * (b0 - bb) / bb * 1e4 if bb else None)
    d0 = dex.px(t0) if dex else None
    for h in HP:
        d1 = dex.px(t0 + h) if dex else None; out.append(s * (d1 / d0 - 1) * 1e4 if d0 and d1 else None)
    v = dex.imb(t0, t0 + 60) if dex else None; out.append(s * v if v is not None else None)
    return out

def work(job):
    key, coin_dirs, sym = job
    fut = r2.load_mid(sym, "fut"); b = bn.load_bin(sym)
    fl = load_flow(sym) if MODE == "flow" else None
    if fut is None or b is None or (MODE == "flow" and fl is None): return key, {"err": "no_series"}
    dex = None
    if coin_dirs:
        pools = r2.read_swaps(coin_dirs)
        allsw = sorted((x[0], x[1], x[2], x[3]) for sw in pools.values() for x in sw if x[3] > 0)
        if len(allsw) >= 50: dex = rev.Dex(allsw)
    rows = []
    for W in CFG[0]:
        base = []
        for t in range(START + W + 10, START + r2.DAY, 10):
            v = sig(fut, b, fl, t, W)
            if v is not None: base.append(abs(v))
        if len(base) < 500: continue
        base.sort(); thr = r2.qthr(base, CFG[1])
        if thr <= 0: continue
        last = -10**9
        for ts in range(SC0, r2.SCORE1 - 4000):
            if ts - last < COOL: continue
            r = sig(fut, b, fl, ts + 1, W)
            if r is None or abs(r) < thr: continue
            s = 1 if r > 0 else -1
            last = ts
            m = stats(fut, b, dex, ts + 1, s, W)
            if m is None: continue
            cs = [c for c in (stats(fut, b, dex, ts + 1 - SHIFT, s, W), stats(fut, b, dex, ts + 1 + SHIFT, s, W)) if c]
            cell = []
            for i, x in enumerate(m):
                cv = [c[i] for c in cs if c[i] is not None]
                cell.append(f"{x:.4f}" if x is not None else "")
                cell.append(f"{x - sum(cv) / len(cv):.4f}" if (x is not None and cv) else "")
            rows.append("\t".join([f"{CFG[2]}{W}", str((ts - r2.T15) // r2.DAY + 15), str(s), f"{abs(r) / thr:.3f}"] + cell))
    os.makedirs(f"{r2.OUT}/coin", exist_ok=True)
    with gzip.open(f"{r2.OUT}/coin/{key}.tsv.gz", "wt") as fh:
        fh.write("\n".join(rows) + "\n")
    return key, {"ok": 1, "n": len(rows), "dex": dex is not None}

def main():
    uni = {}
    for r in csv.DictReader(open(r2.UNIV, encoding="utf-8")): uni.setdefault(r["base"], []).append(r)
    sym2base = {rows[0]["symbol"]: b for b, rows in uni.items()}
    dirs = {}
    for chain in ("ethereum", "bsc", "base"):
        for d in sorted(os.listdir(f"{r2.USD}/{chain}")):
            base, _, suf = d.partition("-"); rows = uni.get(base)
            if not rows or rows[0].get("cg_wrapped") == "1": continue
            if suf and not rows[0]["token"].lower().endswith(suf.lower()): continue
            dirs.setdefault(base, []).append((chain, d))
    jobs = []
    for sym in sorted(os.listdir(bn.BIN)):
        if not os.path.isdir(f"{bn.BIN}/{sym}"): continue
        if not os.path.exists(f"{r2.MID}/fut/2026-10-16/{sym}-2026-10-16.csv.gz"): continue
        b = sym2base.get(sym)
        jobs.append((b if b in dirs else sym, dirs.get(b, []), sym))
    print(f"mode {MODE} coins {len(jobs)} with_dex {sum(1 for j in jobs if j[1])}", flush=True)
    with Pool(r2.P) as pool:
        for i, (key, st) in enumerate(pool.imap_unordered(work, jobs), 1): print(i, key, st, flush=True)
    open(f"{r2.OUT}/DONE.json", "w").write(json.dumps({"coins": len(jobs)}))

if __name__ == "__main__":
    main()
