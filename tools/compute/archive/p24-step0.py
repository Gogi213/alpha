#!/usr/bin/env python3
"""TK-344 П-24 шаг 0 (до исходов): кандидаты P* из кэшей касаний, порог импульса, keep-фильтры. Ни одного исхода не читается.
  p24-step0.py <outdir> [--epochs /data/tk046]
P*: bid, touch_index=0, размер стены (size_max_before*price_tick) >= q90 стен этой монеты за прошлые 7 суток (стена = уникальная
(price_tick, birth_ms), максимум size_max_before; окно по start_ms [t-7д, t)); прогрев 7 суток от первого дня монеты — без ранга; TRX исключён (В-105).
Импульс: approach_1s >= медианы approach_1s по кандидатам q90 янв–сен (пустые — вне импульса). q95 — тот же с q95.
Выход: thresholds.json, keep-q90.csv, keep-q95.csv (symbol,t0_ns,price_tick; t0 = start_ms*1e6), step0-report.json."""
import bisect, csv, glob, json, os, statistics, sys, collections

out = sys.argv[1]
ep = sys.argv[sys.argv.index("--epochs") + 1] if "--epochs" in sys.argv else "/data/tk046"
MON = "jan feb mar apr may jun jul aug sep".split()
WIN = 7 * 86400 * 1000
os.makedirs(out, exist_ok=True)

bysym = collections.defaultdict(list)   # sym -> [(day, path)]
for m in MON:
    for p in sorted(glob.glob(f"{ep}/{m}/home/alpha/epochs/e-{m}/study/approaches/D20/2026-*/touches-*USDT.csv")):
        d = p.split("/")[-2]; s = os.path.basename(p)[8:-4]
        if s != "TRXUSDT":
            bysym[s].append((d, p))


def f(x):
    return float(x) if x != "" else None


def work(item):
    s, files = item
    files.sort()
    rows = []   # bid touches
    for d, p in files:
        with open(p, newline="") as fh:
            for r in csv.DictReader(fh):
                if r["side"] != "bid":
                    continue
                rows.append((int(r["start_ms"]), int(r["price_tick"]), int(r["birth_ms"]), int(r["touch_index"]),
                             float(r["size_max_before"]), f(r["approach_1s"]), d))
    rows.sort()
    if not rows:
        return s, None, None
    first = rows[0][0]
    # стены: (tick,birth) -> (first_start_ms, wall$) ; окно считаем по времени первого касания стены
    walls = {}
    for st, tk, bi, ti, sz, ap, d in rows:
        k = (tk, bi); w = sz * tk
        if k not in walls:
            walls[k] = [st, w]
        else:
            walls[k][1] = max(walls[k][1], w)
    wl = sorted(walls.values())   # по времени первого касания
    wt = [x[0] for x in wl]; ww = [x[1] for x in wl]
    c = []
    win = []   # отсортированные размеры стен окна [st-WIN, st): два указателя вместо sorted() на каждое касание
    lo = hi = 0
    for st, tk, bi, ti, sz, ap, d in rows:
        if ti != 0 or st < first + WIN:
            continue
        while hi < len(wt) and wt[hi] < st:
            bisect.insort(win, ww[hi]); hi += 1
        while lo < hi and wt[lo] < st - WIN:
            del win[bisect.bisect_left(win, ww[lo])]; lo += 1
        if len(win) < 10:
            continue
        wall = sz * tk
        q = lambda p: win[min(len(win) - 1, int(p * len(win)))]
        c.append((d, st, tk, wall, ap, wall >= q(0.90), wall >= q(0.95)))
    return s, c, {"days": len(files), "touches_bid": len(rows), "walls": len(wl), "cand_q90": sum(1 for x in c if x[5])}


cands = {}   # sym -> list of (day, start_ms, price_tick, wall, appr)
meta = {}
if __name__ == "__main__":
    import multiprocessing as mp
    nx = int(os.environ.get("NX", "15"))
    items = sorted(bysym.items(), key=lambda kv: -len(kv[1]))
    with mp.Pool(nx) as pool:
        for s, c, m in pool.imap_unordered(work, items):
            if c is not None:
                cands[s] = c; meta[s] = m
            print(f"done {s} {len(meta)}/{len(items)}", file=sys.stderr, flush=True)

    q90 = [(s, x) for s, c in cands.items() for x in c if x[5]]
    ap90 = [x[4] for s, x in q90 if x[4] is not None]
    med = statistics.median(ap90)
    thr = {"impulse_median_bps": med, "n_q90_candidates": len(q90), "n_q90_with_approach": len(ap90),
           "rule": "approach_1s >= median over q90 candidates Jan-Sep, bid, touch_index 0, TRX excluded"}


    def keep(idx, name):
        sel = [(s, x) for s, c in cands.items() for x in c if x[idx] and x[4] is not None and x[4] >= med]
        with open(f"{out}/{name}", "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n"); w.writerow(["symbol", "t0_ns", "price_tick"])
            for s, x in sorted(sel, key=lambda t: (t[0], t[1][1])):
                w.writerow([s, x[1] * 1_000_000, x[2]])
        return sel


    rep = {"thresholds": thr, "per_symbol": meta}
    for idx, name, lab in ((5, "keep-q90.csv", "q90"), (6, "keep-q95.csv", "q95")):
        sel = keep(idx, name)
        days = collections.Counter(x[0] for s, x in sel)
        sd = collections.Counter((s, x[0]) for s, x in sel)
        permon = collections.Counter(x[0][:7] for s, x in sel)
        rep[lab] = {"n": len(sel), "n_days": len(days), "n_symdays": len(sd), "n_symbols": len({s for s, x in sel}),
                    "per_month": dict(sorted(permon.items())),
                    "per_symbol_top": collections.Counter(s for s, x in sel).most_common(8),
                    "max_share_symbol": max(collections.Counter(s for s, x in sel).values()) / max(1, len(sel))}
    json.dump(thr, open(f"{out}/thresholds.json", "w"), indent=1)
    json.dump(rep, open(f"{out}/step0-report.json", "w"), indent=1, ensure_ascii=False)
    open(f"{out}/step0.done", "w").write("ok\n")
    print(json.dumps({k: rep[k] for k in ("thresholds", "q90", "q95")}, ensure_ascii=False)[:3000])
