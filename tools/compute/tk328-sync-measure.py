#!/usr/bin/env python3
"""TK-328, П-22 §4: замер синхронизации и базиса спот/фьюч. Запуск на calc. Прогоняет `lob sync-basis` по
(месяц × монета × сутки) и сводит в <OUT>/sync-basis.json + sync-basis-raw.csv + sync-basis.done.
Выбор монет как в tk313-u-measure: пересечение спота и фьючерса, равномерно по отсортированному списку.
Использование: tk328-sync-measure.py <OUT> [<бинарник>] [<потоков>]."""
import csv, glob, json, math, os, statistics, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

OUT = sys.argv[1]
BIN = sys.argv[2] if len(sys.argv) > 2 else "/data/tk0328/alpha-tk328-plain"
THREADS = int(sys.argv[3]) if len(sys.argv) > 3 else 3
MONTHS = [("01", "jan"), ("05", "may"), ("09", "sep")]
DAYS = range(5, 11)
NCOIN = 10
FUT = "/data/alpha/epochs/e-%s/root"
SPOT = "/srv/alpha-sda/binlog-spot/e-2026-%s"


def coins(root, mm):
    return {os.path.basename(f).split("-2026-")[0] for f in glob.glob(f"{root}/*-2026-{mm}-05.binlog")}


def parts(root, sym, mm, day):
    return sorted(glob.glob(f"{root}/{sym}-2026-{mm}-{day:02d}*.binlog*"))


def job(t):
    mm, mon, sym, day = t
    s, f = parts(SPOT % mm, sym, mm, day), parts(FUT % mon, sym, mm, day)
    if not s or not f:
        return {"t": t, "err": f"нет частей spot={len(s)} fut={len(f)}"}
    r = subprocess.run([BIN, "lob", "sync-basis", "--spot", *s, "--fut", *f], capture_output=True, text=True)
    if r.returncode != 0:
        return {"t": t, "err": (r.stderr or r.stdout)[-300:]}
    kv = {}
    for line in r.stdout.splitlines():
        p = line.strip().split(",")
        if len(p) >= 3:
            kv[",".join(p[:-1])] = p[-1]
    return {"t": t, "kv": kv}


def num(kv, k):
    try:
        v = float(kv[k])
        return v if math.isfinite(v) else None
    except (KeyError, ValueError):
        return None


def med(v):
    v = [x for x in v if x is not None]
    return statistics.median(v) if v else None


def main():
    os.makedirs(OUT, exist_ok=True)
    tasks = []
    for mm, mon in MONTHS:
        com = sorted(coins(FUT % mon, mm) & coins(SPOT % mm, mm))
        step = max(1, len(com) // NCOIN)
        for sym in com[::step][:NCOIN]:
            for d in DAYS:
                tasks.append((mm, mon, sym, d))
    with ThreadPoolExecutor(THREADS) as ex:
        res = list(ex.map(job, tasks))
    ok = [r for r in res if "kv" in r]
    errs = [r for r in res if "err" in r]
    keys = sorted({k for r in ok for k in r["kv"]})
    with open(f"{OUT}/sync-basis-raw.csv", "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["month", "sym", "day"] + keys)
        for r in ok:
            mm, _, sym, d = r["t"]
            w.writerow([mm, sym, d] + [r["kv"].get(k, "") for k in keys])
    summary = {"tasks": len(tasks), "ok": len(ok), "errors": len(errs), "err_sample": errs[:5], "by_month": {}}
    for mm, _ in MONTHS:
        rows = [r for r in ok if r["t"][0] == mm]
        if not rows:
            continue
        m = {"coin_days": len(rows), "coins": sorted({r["t"][2] for r in rows})}
        for mk in ("spot", "fut"):
            m[f"clock_{mk}"] = {
                "median_ms": med([num(r["kv"], f"clock,{mk},median_ms") for r in rows]),
                "p99_ms_median": med([num(r["kv"], f"clock,{mk},p99_ms") for r in rows]),
                "p99_ms_max": max([x for x in (num(r["kv"], f"clock,{mk},p99_ms") for r in rows) if x is not None], default=None),
                "share_zero_median": med([num(r["kv"], f"clock,{mk},share_zero") for r in rows]),
                "share_negative_median": med([num(r["kv"], f"clock,{mk},share_negative") for r in rows]),
                "rejected_total": sum(num(r["kv"], f"clock,{mk},rejected") or 0 for r in rows),
            }
        lag = {}
        for k in range(-5, 6):
            c = [num(r["kv"], f"lag,{k}") for r in rows]
            lag[k] = med(c)
        best = [max(range(-5, 6), key=lambda k: num(r["kv"], f"lag,{k}") if num(r["kv"], f"lag,{k}") is not None else -2) for r in rows]
        m["lag_corr_median"] = lag
        m["lag_argmax_median_of_days"] = statistics.median(best)
        m["lag_argmax_hist"] = {k: best.count(k) for k in sorted(set(best))}
        m["basis_bps"] = {q: med([num(r["kv"], f"basis,{q}") for r in rows]) for q in ("p5", "p50", "p95")}
        m["basis_bps_range_p50"] = [min(x for x in (num(r["kv"], "basis,p50") for r in rows) if x is not None),
                                    max(x for x in (num(r["kv"], "basis,p50") for r in rows) if x is not None)]
        for w in ("drift30m", "drift4h"):
            m[w] = {q: med([num(r["kv"], f"{w},{q}") for r in rows]) for q in ("std", "p5", "p95")}
        # по монетам: медиана базиса и дрейфа
        bysym = {}
        for sym in m["coins"]:
            rs = [r for r in rows if r["t"][2] == sym]
            bysym[sym] = {"basis_p5": med([num(r["kv"], "basis,p5") for r in rs]),
                          "basis_p50": med([num(r["kv"], "basis,p50") for r in rs]),
                          "basis_p95": med([num(r["kv"], "basis,p95") for r in rs]),
                          "drift30m_std": med([num(r["kv"], "drift30m,std") for r in rs]),
                          "drift4h_std": med([num(r["kv"], "drift4h,std") for r in rs])}
        m["by_coin"] = bysym
        summary["by_month"][mm] = m
    json.dump(summary, open(f"{OUT}/sync-basis.json", "w"), indent=1, ensure_ascii=False)
    open(f"{OUT}/sync-basis.done", "w").write("done\n")


main()
