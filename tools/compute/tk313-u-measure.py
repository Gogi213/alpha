#!/usr/bin/env python3
"""TK-313, П-21 §4: пол usd_min спота u1..u3 = квантили номинала стен bid спота, где фьючерсные 30k/100k/300k стоят
в распределении фьючерса. Запуск на calc под alsched. Выход: <OUT>/u-measure.json + u-measure.done.
Номинал стены = size_at_touch(лоты) * step * price_tick * tick (tick/step из заголовка binlog: magic4|ver1|tick_e9 8|step_e9 8).
Стена = (монета, сутки, price_tick, birth_ms) bid, берётся первое касание. Общий нижний срез CUT $ (touches --h3-mode notional)."""
import csv, glob, json, os, struct, subprocess, sys
from concurrent.futures import ThreadPoolExecutor

BIN = "/opt/alpha-compute/bin/alpha-tk312-pgo"
OUT = sys.argv[1]
CUT = 5000.0
MONTHS = [("01", "jan"), ("05", "may"), ("09", "sep")]
DAYS = range(5, 11)
NCOIN = 10
FUT = "/data/alpha/epochs/e-%s/root"
SPOT = "/srv/alpha-sda/binlog-spot/e-2026-%s"
FUTS = [30000, 100000, 300000]


def quant(v, q):
    v = sorted(v)
    return v[min(len(v) - 1, int(q * len(v)))]


def below(v, u):
    return sum(1 for x in v if x < u) / len(v)


def coins(root, mm):
    s = set()
    for f in glob.glob(f"{root}/*-2026-{mm}-05.binlog"):
        s.add(os.path.basename(f).split("-2026-")[0])
    return s


def hdr(path):
    with open(path, "rb") as f:
        b = f.read(21)
    return struct.unpack("<qq", b[5:21])


def job(t):
    mkt, mm, day, sym, root, mon = t
    date = f"2026-{mm}-{day:02d}"
    files = sorted(glob.glob(f"{root}/{sym}-{date}*.binlog"))
    if not files:
        return None
    w = f"{OUT}/w/{mkt}-{mm}-{sym}-{day:02d}"
    os.makedirs(w, exist_ok=True)
    for f in files:
        d = f"{w}/{os.path.basename(f)}"
        if not os.path.lexists(d):
            os.symlink(f, d)
    st = f"{root}/verify-{sym}.status"
    unver = []
    if os.path.exists(st):
        if not os.path.lexists(f"{w}/verify-{sym}.status"):
            os.symlink(st, f"{w}/verify-{sym}.status")
    else:
        unver = ["--allow-unverified"]
    ins = f"{root}/instruments.csv"
    if os.path.exists(ins) and not os.path.lexists(f"{w}/instruments.csv"):
        os.symlink(ins, f"{w}/instruments.csv")
    out = f"{w}/t.csv"
    r = subprocess.run([BIN, "lob", "touches", "--root", w, "--symbol", sym, "--h3-mode", "notional", "--h3-usd", str(CUT),
                        "--out", out] + unver, capture_output=True, text=True)
    if r.returncode != 0:
        return {"t": t, "err": (r.stderr or r.stdout)[-300:]}
    tick_e9, step_e9 = hdr(files[0])
    vals = {}
    with open(out, newline="") as fh:
        for row in csv.DictReader(fh):
            if row["side"] != "bid":
                continue
            k = (row["price_tick"], row["birth_ms"])
            if k in vals:
                continue
            vals[k] = int(row["size_at_touch"]) * (step_e9 / 1e9) * int(row["price_tick"]) * (tick_e9 / 1e9)
    return {"t": t, "n": len(vals), "v": list(vals.values()), "unver": bool(unver)}


def main():
    os.makedirs(OUT, exist_ok=True)
    tasks = []
    for mm, mon in MONTHS:
        com = sorted(coins(FUT % mon, mm) & coins(SPOT % mm, mm))
        step = max(1, len(com) // NCOIN)
        pick = com[::step][:NCOIN]
        for sym in pick:
            for d in DAYS:
                tasks.append(("fut", mm, d, sym, FUT % mon, mon))
                tasks.append(("spot", mm, d, sym, SPOT % mm, mon))
    with ThreadPoolExecutor(15) as ex:
        res = [r for r in ex.map(job, tasks) if r]
    errs = [r for r in res if "err" in r]
    ok = [r for r in res if "v" in r]
    summary = {"cut_usd": CUT, "tasks": len(tasks), "errors": len(errs), "err_sample": errs[:3]}
    for mkt in ("fut", "spot"):
        v = [x for r in ok if r["t"][0] == mkt for x in r["v"]]
        summary[mkt] = {"walls": len(v), "quantiles_usd": {q: quant(v, q) for q in (.1, .25, .5, .75, .9)} if v else {}}
    f = [x for r in ok if r["t"][0] == "fut" for x in r["v"]]
    s = [x for r in ok if r["t"][0] == "spot" for x in r["v"]]
    if f and s:
        qs = [below(f, u) for u in FUTS]
        summary["fut_quantile_of_30k_100k_300k"] = qs
        summary["u1_u2_u3_usd"] = [quant(s, q) for q in qs]
        summary["by_month"] = {}
        for mm, _ in MONTHS:
            fm = [x for r in ok if r["t"][:2] == ("fut", mm) for x in r["v"]]
            sm = [x for r in ok if r["t"][:2] == ("spot", mm) for x in r["v"]]
            if fm and sm:
                q = [below(fm, u) for u in FUTS]
                summary["by_month"][mm] = {"walls_fut": len(fm), "walls_spot": len(sm), "q": q,
                                            "u": [quant(sm, x) for x in q]}
    json.dump(summary, open(f"{OUT}/u-measure.json", "w"), indent=1, ensure_ascii=False)
    open(f"{OUT}/u-measure.done", "w").write("done\n")


main()
