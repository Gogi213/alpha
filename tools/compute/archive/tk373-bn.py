#!/usr/bin/env python3
"""TK-373 / P-25: Binance USDM -> Bybit/DEX. Событие = резкий ход цены Binance (последняя сделка за секунду, aggTrades) за W с:
|ход| >= q95 своей монеты по 16.10 (порог фиксирован до окна счёта 17–21.10; не чаще раза в 60 с). s = знак хода. После t0=ts+1 (10/60/300 с):
на DEX imb, dpx (как tk373-rev.py), на Bybit by = s*ход середины; на Binance bn = s*ход дальше. Пред: bypre = s*ход середины Bybit за [t0-W,t0].
Контроль — те же моменты t±3600 с (net = M - среднее контролей). Колонки 0..23 как у tk373-rev.py (читает tk373-dir.py), 24..25 bypre M/net, 26..31 bn10/60/300 M/net.
usage: tk373-bn.py <usd> <sec> <universe.csv> <outdir> [P=15]   (binsec: /data/tk0373/binsec/<SYM>/2026-10-DD.csv = 'сек,цена')"""
import sys, os, json, gzip, csv, importlib.util
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("r2", os.path.join(HERE, "tk369-run2.py"))
r2 = importlib.util.module_from_spec(spec); sys.modules["r2"] = r2; spec.loader.exec_module(r2)
rv = importlib.util.spec_from_file_location("rev", os.path.join(HERE, "tk373-rev.py"))
rev = importlib.util.module_from_spec(rv); rv.loader.exec_module(rev)
BIN = os.environ.get("BINSEC", "/data/tk0373/binsec")
WS = (10, 60); HP = (10, 60, 300); SHIFT = 3600; COOL = 60
START = r2.T15 + r2.DAY               # порог — 16.10
SC0 = r2.T15 + 2 * r2.DAY             # счёт 17.10 00:00 .. 22.10 00:00 - 4000 с

def load_bin(sym):
    arr = [0.0] * r2.NSEC; have = 0
    for d in range(15, 22):
        f = f"{BIN}/{sym}/2026-10-{d}.csv"
        if not os.path.exists(f): continue
        have += 1
        for ln in open(f):
            a = ln.split(",")
            i = int(a[0]) - r2.T15
            if 0 <= i < r2.NSEC: arr[i] = float(a[1])
    if not have: return None
    last = 0.0
    for i in range(r2.NSEC):                  # вперёд-заполнение; arr[i] = состояние на конец секунды t_s
        if arr[i] > 0: last = arr[i]
        else: arr[i] = last
    return arr

def bp(arr, t):
    i = t - 1 - r2.T15
    if i < 0 or i >= r2.NSEC: return None
    return arr[i] if arr[i] > 0 else None

def stats(fut, bn, dex, t0, s, W):
    p0 = r2.pm(fut, t0); b0 = bp(bn, t0)
    if p0 is None or b0 is None: return None
    out = []
    d0 = dex.px(t0) if dex else None
    v = dex.imb(t0 - W, t0) if dex else None; out.append(s * v if v is not None else None)
    for h in HP:
        v = dex.imb(t0, t0 + h) if dex else None; out.append(s * v if v is not None else None)
        d1 = dex.px(t0 + h) if dex else None
        out.append(s * (d1 / d0 - 1) * 1e4 if d0 and d1 else None)
        p1 = r2.pm(fut, t0 + h)
        out.append(s * (p1 - p0) / p0 * 1e4 if p1 else None)
    pb = r2.pm(fut, t0 - W)
    out.append(s * (p0 - pb) / pb * 1e4 if pb else None)
    for h in HP:
        b1 = bp(bn, t0 + h)
        out.append(s * (b1 - b0) / b0 * 1e4 if b1 else None)
    return out

def work(job):
    key, coin_dirs, sym = job
    fut = r2.load_mid(sym, "fut"); bn = load_bin(sym)
    if fut is None or bn is None: return key, {"err": "no_series"}
    dex = None
    if coin_dirs:
        pools = r2.read_swaps(coin_dirs)
        allsw = sorted((x[0], x[1], x[2], x[3]) for sw in pools.values() for x in sw if x[3] > 0)
        if len(allsw) >= 50: dex = rev.Dex(allsw)
    rows = []
    for W in WS:
        base = []
        for t in range(START + W + 10, START + r2.DAY, 10):
            a, b = bp(bn, t), bp(bn, t - W)
            if a and b: base.append(abs((a - b) / b * 1e4))
        if len(base) < 500: continue
        base.sort(); thr = r2.qthr(base, .95)
        if thr <= 0: continue
        last = -10**9
        for ts in range(SC0, r2.SCORE1 - 4000):
            if ts - last < COOL: continue
            a, b = bp(bn, ts + 1), bp(bn, ts + 1 - W)
            if not a or not b: continue
            r = (a - b) / b * 1e4
            if abs(r) < thr: continue
            s = 1 if r > 0 else -1
            last = ts
            m = stats(fut, bn, dex, ts + 1, s, W)
            if m is None: continue
            cs = [c for c in (stats(fut, bn, dex, ts + 1 - SHIFT, s, W), stats(fut, bn, dex, ts + 1 + SHIFT, s, W)) if c]
            cell = []
            for i, x in enumerate(m):
                cv = [c[i] for c in cs if c[i] is not None]
                cell.append(f"{x:.4f}" if x is not None else "")
                cell.append(f"{x - sum(cv) / len(cv):.4f}" if (x is not None and cv) else "")
            rows.append("\t".join([f"R{W}", str((ts - r2.T15) // r2.DAY + 15), str(s), f"{abs(r) / thr:.3f}"] + cell))
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
    for sym in sorted(os.listdir(BIN)):
        if not os.path.isdir(f"{BIN}/{sym}"): continue
        if not os.path.exists(f"{r2.MID}/fut/2026-10-16/{sym}-2026-10-16.csv.gz"): continue
        b = sym2base.get(sym)
        jobs.append((b if b in dirs else sym, dirs.get(b, []), sym))
    print(f"coins {len(jobs)} with_dex {sum(1 for j in jobs if j[1])}", flush=True)
    with Pool(r2.P) as pool:
        for i, (key, st) in enumerate(pool.imap_unordered(work, jobs), 1): print(i, key, st, flush=True)
    open(f"{r2.OUT}/DONE.json", "w").write(json.dumps({"coins": len(jobs)}))

if __name__ == "__main__":
    main()
