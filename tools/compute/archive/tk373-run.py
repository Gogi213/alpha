#!/usr/bin/env python3
"""TK-373 / P-25: контроль «ход Bybit ДО события» для L (дисбаланс потока DEX) и F (расхождение цены пула и середины Bybit).
Движок — tk369-run2.py (импорт: загрузка середины, события L/F, маркаут с вычетом сдвигов ±3600); здесь только L/F-события и
построчная запись: hid, сутки, s, сила, pre10/pre60/pre300 (ход середины Bybit за W с до t0=ts+1 по стороне события, бп), Snet(v=1) по H.
usage: tk373-run.py <usd> <sec> <universe.csv> <outdir> [P=14]    (argv — как у tk369-run2.py; агрегация tk373-agg.py)"""
import sys, os, json, bisect, gzip, math, importlib.util
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("r2", os.path.join(HERE, "tk369-run2.py"))
r2 = importlib.util.module_from_spec(spec); sys.modules["r2"] = r2; spec.loader.exec_module(r2)
PRE_W = (10, 60, 300)

def work(job):
    key, coin_dirs, sym, tick = job
    fut = r2.load_mid(sym, "fut")
    if fut is None: return key, {"err": "no_fut_mid"}
    pools = r2.read_swaps(coin_dirs)
    c_px = tick / 2.0
    ev = []
    for pk, sw in pools.items():
        for e in r2.events_imb(sw): ev.append(e)
    allsw = sorted((x[0], x[1], x[3]) for sw in pools.values() for x in sw if x[3] > 0)
    pt = [x[0] for x in allsw]; pp = [x[2] for x in allsw]
    bt = sorted(t for sw in pools.values() for t, _ in r2.big_times(sw))
    pm = r2.pm; MIN_PRIOR = r2.MIN_PRIOR; qthr = r2.qthr
    if allsw:
        first = []
        for ts, s_, pu in allsw[:200]:
            m_ = pm(fut, ts)
            if m_: first.append(pu / (m_ * c_px))
        if first:
            first.sort(); med = first[len(first) // 2]
            f = 10 ** round(math.log10(med)) if med > 0 else 1
            hist = []; last_fire = {}
            for ts, s_, pu in allsw:
                m_ = pm(fut, ts)
                if not m_: continue
                d = (pu / f / (m_ * c_px) - 1) * 1e4
                a_ = abs(d)
                if len(hist) >= MIN_PRIOR and a_ < 2000:
                    sg = 1 if d > 0 else -1
                    for hid, q, hb, hp in (("F024", .90, "F025", "F026"), ("F027", .99, "F028", "F029")):
                        t = qthr(hist, q)
                        if a_ >= t > 0:
                            st = a_ / t
                            ev.append((hid, ts, sg, st))
                            j = bisect.bisect_left(bt, ts - 60)
                            if j < len(bt) and bt[j] <= ts: ev.append((hb, ts, sg, st))
                            if ts - last_fire.get(hp, -10**9) >= 10:
                                ok = True
                                for k in range(1, 10):
                                    i1 = bisect.bisect_right(pt, ts - k) - 1
                                    mk_ = pm(fut, ts - k)
                                    if i1 < 0 or not mk_: ok = False; break
                                    dk = (pp[i1] / f / (mk_ * c_px) - 1) * 1e4
                                    if dk * sg <= 0 or abs(dk) < t: ok = False; break
                                if ok:
                                    last_fire[hp] = ts; ev.append((hp, ts, sg, st))
                bisect.insort(hist, a_)
    rows = []; cache = {}
    for hid, ts, s_, st in ev:
        if not (r2.SCORE0 <= ts < r2.SCORE1): continue
        ck = (ts, s_)
        r = cache.get(ck)
        if r is None:
            r = r2.markout(fut, ts, s_); cache[ck] = r
        if r[1] is None: continue
        t0 = ts + 1; p0 = pm(fut, t0)
        pres = []
        for w in PRE_W:
            pw = pm(fut, t0 - w)
            pres.append(f"{s_ * (p0 - pw) / pw * 1e4:.3f}" if pw else "")
        sn = [f"{m - c:.3f}" if (m is not None and c is not None) else "" for m, c in r[1]]
        rows.append("\t".join([hid, str((ts - r2.T15) // r2.DAY + 15), str(s_), f"{st:.3f}"] + pres + sn))
    os.makedirs(f"{r2.OUT}/coin", exist_ok=True)
    with gzip.open(f"{r2.OUT}/coin/{key}.tsv.gz", "wt") as fh:
        fh.write("\n".join(rows) + "\n")
    return key, {"ok": 1, "n": len(rows)}

def main():
    r2.TICK = {}
    import csv
    TICK = {r["symbol"]: float(r["tick_size"]) for r in csv.DictReader(open(os.environ.get("TICKCSV", "/srv/alpha-sda/tk366w/instruments.csv"), encoding="utf-8"))}
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
    jobs = [(b, v["dirs"], v["sym"], TICK[v["sym"]]) for b, v in sorted(coins.items())]
    jobs.sort(key=lambda j: j[0] != "Q")        # тяжёлая монета первой
    print(f"coins {len(jobs)}", flush=True)
    with Pool(r2.P) as pool:
        for i, (key, st) in enumerate(pool.imap_unordered(work, jobs), 1): print(i, key, st, flush=True)
    open(f"{r2.OUT}/DONE.json", "w").write(json.dumps({"coins": len(jobs)}))

if __name__ == "__main__":
    main()
