#!/usr/bin/env python3
"""TK-352 G0: таблица n / n_эфф / MDE_P по ячейкам (R5.3, R5.10 п.2–3). Ни одного исхода не читается; числа — G013-scope.json.
  g013-g0-table.py <outdir>      env NX (по умолчанию 15)
События: первые касания стены (touch_index 0) и повторные (1, 2+) из кэшей касаний янв–сен; обе стороны; fut и spot раздельно.
Стена = (сторона, price_tick, birth_ms); размер = max size_max_before по касаниям. Ранг монеты = доля стен окна [t-7д, t) (обе стороны
вместе, по времени первого касания) с размером < размера стены; окно < 50 стен или первые 7 суток записи монеты — «без ранга».
Импульс: approach_1s >= 3.271 бп (G013-scope.json); пусто = «нет подхода». Стейбл: медиана sigma240 монеты (янв–сен) < 38.2.
n_эфф = число различных (монета, сутки) со событиями ячейки (кластер: общий режим дня и соседние стены монеты); контроль C3: k=1, без
возврата => n_эфф,контр = n_эфф. MDE_P = 2.80*sqrt(p0(1-p0)*2/n_эфф), p0 = 0.5; ячейка определена, если MDE_P <= 0.25 (dp*).
Выход: g0-cells.csv, g0-summary.json, g0-coins.csv, g0.done."""
import bisect, csv, glob, json, math, os, statistics, sys, collections

out = sys.argv[1]
os.makedirs(out, exist_ok=True)
MON = "jan feb mar apr may jun jul aug sep".split()
WIN = 7 * 86400 * 1000
IMPULSE = 3.271074
STABLE = 38.2
DP = 0.25
P0 = 0.5
MINW = 50
FUT = "/data/tk046/{m}/home/alpha/epochs/e-{m}/study/approaches/D20/2026-*/touches-*USDT.csv"
SPOT = "/data/tk0319/jrn/2026-*/wt/sub/D20/2026-*/touches-*USDT.csv"
SIGF = "/data/tk046/{m}/home/alpha/epochs/e-{m}/study/sigma240/sigma-{s}.csv"
SIGS = "/data/tk0319/e-{m}/study/sigma240/sigma-{s}.csv"


def files(mode):
    by = collections.defaultdict(list)
    pats = [FUT.format(m=m) for m in MON] if mode == "fut" else [SPOT]
    for pat in pats:
        for p in glob.glob(pat):
            s = os.path.basename(p)[8:-4]
            if s != "TRXUSDT":
                by[s].append((p.split("/")[-2], p))
    return by


def fl(x):
    return float(x) if x != "" else None


def work(a):
    mode, s, fl_ = a
    fl_.sort()
    rows = []
    for d, p in fl_:
        with open(p, newline="") as fh:
            r = csv.reader(fh)
            h = next(r)
            ix = {c: i for i, c in enumerate(h)}
            iS, iT, iB, iTI, iM, iA, iSt = (ix[c] for c in ("side", "price_tick", "birth_ms", "touch_index", "size_max_before", "approach_1s", "start_ms"))
            for x in r:
                rows.append((int(x[iSt]), x[iS], int(x[iT]), int(x[iB]), int(x[iTI]), float(x[iM]), fl(x[iA]) if x[iA] != "" else None, d))
    rows.sort()
    if not rows:
        return mode, s, [], {}
    first = rows[0][0]
    walls = {}
    for st, sd, tk, bi, ti, sz, ap, d in rows:
        k = (sd, tk, bi)
        w = sz * tk
        if k not in walls:
            walls[k] = [st, w]
        elif w > walls[k][1]:
            walls[k][1] = w
    wl = sorted(walls.values())
    wt = [x[0] for x in wl]
    ww = [x[1] for x in wl]
    win = []
    lo = hi = 0
    ev = []   # (day, rank|None, ti_bin, impulse)
    for st, sd, tk, bi, ti, sz, ap, d in rows:
        while hi < len(wt) and wt[hi] < st:
            bisect.insort(win, ww[hi]); hi += 1
        while lo < hi and wt[lo] < st - WIN:
            del win[bisect.bisect_left(win, ww[lo])]; lo += 1
        rk = None
        if st >= first + WIN and len(win) >= MINW:
            rk = bisect.bisect_left(win, sz * tk) / len(win)
        imp = "none" if ap is None else ("impulse" if ap >= IMPULSE else "calm")
        ev.append((d, rk, min(ti, 2), imp))
    # sigma240 монеты: медиана по всем значениям янв–сен
    vals = []
    for m in MON:
        p = (SIGF if mode == "fut" else SIGS).format(m=m, s=s)
        if os.path.exists(p):
            with open(p, newline="") as fh:
                r = csv.reader(fh); next(r)
                vals.extend(float(x[1]) for x in r if len(x) > 1 and x[1] != "")
    sig = statistics.median(vals) if vals else None
    meta = {"days": len(fl_), "touches": len(rows), "walls": len(wl), "sigma240_median": sig,
            "events_no_rank": sum(1 for e in ev if e[1] is None)}
    return mode, s, ev, meta


def rbin(r):
    if r is None:
        return "no_rank"
    return "r<0.5" if r < 0.5 else "r0.5-0.9" if r < 0.9 else "r0.9-0.95" if r < 0.95 else "r>=0.95"


def mde(n):
    return 2.80 * math.sqrt(P0 * (1 - P0) * 2 / n) if n > 0 else None


if __name__ == "__main__":
    import multiprocessing as mp
    nx = int(os.environ.get("NX", "15"))
    jobs = []
    for mode in ("fut", "spot"):
        for s, f in files(mode).items():
            jobs.append((mode, s, f))
    jobs.sort(key=lambda j: -len(j[2]))
    cells = collections.defaultdict(lambda: [0, set(), set()])   # key -> [n, symdays, days]
    coins = []
    nd = 0
    with mp.Pool(nx) as pool:
        for mode, s, ev, meta in pool.imap_unordered(work, jobs):
            nd += 1
            print(f"done {mode} {s} {nd}/{len(jobs)}", file=sys.stderr, flush=True)
            if not meta:
                continue
            stable = meta["sigma240_median"] is not None and meta["sigma240_median"] < STABLE
            band = meta["sigma240_median"] is not None and 19.1 <= meta["sigma240_median"] <= 76.4
            coins.append((mode, s, meta, stable, band))
            for d, rk, ti, imp in ev:
                for mon in (d[:7], "all"):
                    for cumul in (False, True):
                        if cumul and (rk is None or rk < 0.9):
                            continue
                        rb = "r>=0.9" if cumul else rbin(rk)
                        k = (mode, mon, "stable" if stable else "other", rb, ti, imp)
                        c = cells[k]
                        c[0] += 1; c[1].add((s, d)); c[2].add(d)
    with open(f"{out}/g0-cells.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["mode", "month", "stratum", "rank_bin", "touch_idx", "approach", "n_events", "n_symdays_eff", "n_days", "mde_p", "defined"])
        for k in sorted(cells):
            n, sd, dd = cells[k]
            m = mde(len(sd))
            w.writerow(list(k) + [n, len(sd), len(dd), f"{m:.4f}", int(m <= DP)])
    with open(f"{out}/g0-coins.csv", "w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["mode", "symbol", "days", "touches", "walls", "sigma240_median", "stable", "in_band_19.1_76.4", "events_no_rank"])
        for mode, s, m, st, bd in sorted(coins, key=lambda c: (c[0], c[1])):
            w.writerow([mode, s, m["days"], m["touches"], m["walls"], m["sigma240_median"], int(st), int(bd), m["events_no_rank"]])
    # сводка: основная ячейка и распределение стен
    summ = {"impulse_bps": IMPULSE, "stable_sigma_lt": STABLE, "dp_star": DP, "n_eff_min_for_defined": math.ceil((2.80 ** 2) * 0.5 / DP ** 2)}
    for mode in ("fut", "spot"):
        cs = [c for c in coins if c[0] == mode]
        summ[mode] = {"coins": len(cs), "stable_coins": [c[1] for c in cs if c[3]], "band_coins": [c[1] for c in cs if c[4]],
                      "walls_per_coin_quartiles": statistics.quantiles([c[2]["walls"] for c in cs], n=4) if len(cs) > 3 else None,
                      "events_total": sum(c[2]["touches"] for c in cs), "events_no_rank": sum(c[2]["events_no_rank"] for c in cs)}
    json.dump(summ, open(f"{out}/g0-summary.json", "w"), indent=1, ensure_ascii=False)
    open(f"{out}/g0.done", "w").write("ok\n")
    print(json.dumps(summ, ensure_ascii=False)[:2000])
