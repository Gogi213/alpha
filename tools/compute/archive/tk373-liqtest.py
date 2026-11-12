#!/usr/bin/env python3
"""TK-373 / P-25 H022-H023: крупное добавление/вывод ликвидности пула -> модуль хода середины Bybit после события.
Знака у гипотезы нет (sign_rule: add/remove -> «нет знака (сила)») -> мера = |ход Bybit| за h=10/60/300 с после t0=ts+1 (бп) против контроля (те же моменты t0±3600 с), net = M - среднее контролей;
плюс |ход| за 60 с ДО события (pre) — кластеризация волатильности: событие может лишь идти за всплеском.
Размер: v3 - ликвидность L (слово data), v2 - sqrt(a0*a1); L=0 (poke) пропущены. Порог q95/q99 - по своему пулу и классу (add=mint, rem=burn) за сутки 16.10 (causal, пулы с >=30 событий); счёт 17-21.10; не чаще раза в 60 с на монету и класс.
usage: tk373-liqtest.py <usd> <sec> <universe.csv> <outdir> [P]   env LIQ=/data/tk0373/liq CHAINS=ethereum,base  (движок tk369-run2.py)"""
import sys, os, json, glob, gzip, importlib.util, csv, math
from multiprocessing import Pool
HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("r2", os.path.join(HERE, "tk369-run2.py"))
r2 = importlib.util.module_from_spec(spec); sys.modules["r2"] = r2; spec.loader.exec_module(r2)
LIQ = os.environ.get("LIQ", "/data/tk0373/liq"); CHAINS = os.environ.get("CHAINS", "ethereum,base").split(",")
HP = (10, 60, 300); SHIFT = 3600; COOL = 60; PRE = 60; QS = (0.95, 0.99); MINTHR = 30
T16 = r2.T15 + r2.DAY; T17 = r2.T15 + 2 * r2.DAY

def read_liq(chain, d):
    ev = {}   # pool dir -> list (ts, cls, size)
    out = []
    for f in sorted(glob.glob(f"{LIQ}/{chain}/{d}/2026-10-*.csv")):
        for ln in open(f).read().split("\n")[1:]:
            a = ln.split(",")
            if len(a) < 6: continue
            try:
                ts = int(a[1]); kind = a[4]; h = a[5][2:] if a[5].startswith("0x") else a[5]
                w = [int(h[i:i + 64], 16) for i in range(0, len(h) - 63, 64)]
                if kind == "mint3": sz = w[1]
                elif kind == "burn3": sz = w[0]
                elif kind in ("mint2", "burn2"): sz = math.sqrt(w[-2] * w[-1])
                else: continue
            except (ValueError, IndexError):
                continue
            if sz > 0: out.append((ts, "add" if kind.startswith("mint") else "rem", float(sz), f[-14:-4]))
    return out

def absmove(fut, t0, h):
    p0 = r2.pm(fut, t0); p1 = r2.pm(fut, t0 + h)
    return abs((p1 - p0) / p0 * 1e4) if p0 and p1 else None

def stats(fut, t0):
    out = [absmove(fut, t0 - PRE, PRE)]
    out += [absmove(fut, t0, h) for h in HP]
    return out

def work(job):
    key, coin_dirs, sym = job
    fut = r2.load_mid(sym, "fut")
    if fut is None: return key, {"err": "no_fut_mid"}, []
    ev = []
    for chain, d in coin_dirs:
        for ts, cls, sz, day in read_liq(chain, d): ev.append((chain, d, cls, ts, sz))
    rows = []
    for (chain, d, cls) in sorted({(e[0], e[1], e[2]) for e in ev}):
        pe = sorted((e[3], e[4]) for e in ev if e[:3] == (chain, d, cls))
        base = sorted(sz for ts, sz in pe if T16 <= ts < T17)
        if len(base) < MINTHR: continue
        for q in QS:
            thr = r2.qthr(base, q)
            last = -10**9
            for ts, sz in pe:
                if ts < T17 or ts >= r2.SCORE1 - 4000 or sz < thr or ts - last < COOL: continue
                last = ts
                m = stats(fut, ts + 1)
                cs = [c for c in (stats(fut, ts + 1 - SHIFT), stats(fut, ts + 1 + SHIFT)) if c]
                vals = []
                for i, x in enumerate(m):
                    cv = [c[i] for c in cs if c[i] is not None]
                    vals += [x, (x - sum(cv) / len(cv)) if (x is not None and cv) else None]
                rows.append((chain, key, cls, q, (ts - r2.T15) // r2.DAY + 15, d, vals))
    return key, {"ok": 1, "n": len(rows)}, rows

def main():
    TICK = {r["symbol"]: 1 for r in csv.DictReader(open(os.environ.get("TICKCSV", "/srv/alpha-sda/tk366w/instruments.csv"), encoding="utf-8"))}
    uni = {}
    for r in csv.DictReader(open(r2.UNIV, encoding="utf-8")): uni.setdefault(r["base"], []).append(r)
    coins = {}
    for chain in CHAINS:
        for d in sorted(os.listdir(f"{LIQ}/{chain}")):
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
    os.makedirs(r2.OUT, exist_ok=True)
    with Pool(r2.P) as pool, gzip.open(f"{r2.OUT}/events.tsv.gz", "wt") as fh:
        fh.write("chain\tcoin\tcls\tq\tday\tpool\tpre_M\tpre_net\tb10_M\tb10_net\tb60_M\tb60_net\tb300_M\tb300_net\n")
        for i, (key, st, rows) in enumerate(pool.imap_unordered(work, jobs), 1):
            print(i, key, st, flush=True)
            for ch, k, cls, q, day, d, v in rows:
                fh.write("\t".join([ch, k, cls, str(q), str(day), d] + [f"{x:.4f}" if x is not None else "" for x in v]) + "\n")
    open(f"{r2.OUT}/DONE.json", "w").write(json.dumps({"coins": len(jobs)}))

if __name__ == "__main__":
    main()
