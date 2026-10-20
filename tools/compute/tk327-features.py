#!/usr/bin/env python3
"""TK-327: признаки убытка для сделок спота S0 (TK-313) и фьюча B1 (половина А, psim cap0), без повтора прохода (В-213).
Из готового join-ом: usd стены, сила В-67 (size_at_arm / flow_1h_lots, %), σ240, btc4h-режим на t0 сигнала;
по ленте (`lob trades`, один раз на монета-сутки): цена через 1/5/30 с после ПРОКСИ входа.
Прокси входа: rounds не хранят время заполнения -> t_in = первая сделка-продажа с ценой <= entry_px (vwap заполненных ног)
после t0 (раньше этого момента лесенка не могла дойти до средней цены). "mid" = последняя сделка (цена) на момент t_in+Δ.
ret_Δ_bps = (p - entry_px)/entry_px*1e4*dir: минус = цена пошла против нас (выбивает).
Запуск (calc):  tk327-features.py spot|fut <out.csv> [--workers N] [--limit-days K]
"""
import csv, glob, json, os, struct, subprocess, sys, tempfile, bisect, hashlib
from array import array
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

MODE = sys.argv[1]
OUT = sys.argv[2]
WORKERS = int(sys.argv[sys.argv.index("--workers") + 1]) if "--workers" in sys.argv else 4
LIMIT = int(sys.argv[sys.argv.index("--limit-days") + 1]) if "--limit-days" in sys.argv else 0
BIN = "/opt/alpha-compute/bin/alpha-tk312-pgo"
B1F = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
MONS = {"01": "jan", "02": "feb", "03": "mar", "04": "apr", "05": "may", "06": "jun", "07": "jul", "08": "aug", "09": "sep"}
HALF_B = os.environ.get("TK327_HALF") == "B"   # П-23 (TK-331): проверка на половине B; порог не пересчитывается
NOTAPE = os.environ.get("TK327_NOTAPE") == "1"  # без ленты: нужны только σ240/net (П-23)
isA = lambda s: (int(hashlib.sha256(s.encode()).hexdigest(), 16) % 2 == 0) != HALF_B
DELTAS = (1, 5, 30)


def read(path):
    with open(path, newline="") as f:
        return list(csv.DictReader(l for l in f if not l.startswith("#")))


def paths(day):
    mo = MONS[day[5:7]]
    if MODE == "spot":
        return dict(root=f"/data/tk0319/e-{mo}/study/root-{day}", sigma="/data/tk0319/sigma240",
                    regime=f"/data/tk0319/regime/{mo}/{day}.csv", appr=f"/data/tk0319/jrn/{day}/wt/sub/D20/{day}",
                    inst="/data/tk0319/instruments.csv")
    e = f"/data/tk046/{mo}/home/alpha/epochs/e-{mo}"
    return dict(root=f"{e}/study/root-{day}", sigma=f"{e}/study/sigma240", regime=f"/data/tk0196/regime/{mo}/{day}.csv",
                appr=f"/data/p12r3a/jrn/{day}/wt/sub/D20/{day}", inst=f"{e}/study/root-{day}/instruments.csv")


# --- список сделок -------------------------------------------------------
trades = []   # dict: sym, day, r (rounds row), sig (signals row | None)
if MODE == "spot":
    for rd in sorted(glob.glob("/data/tk0313/out/run-*")):
        day = "-".join(rd.rsplit("-", 3)[-3:])
        d = f"{rd}/out/{day}/s0"
        if not os.path.exists(f"{d}/rounds.csv"):
            continue
        sig = {(s["symbol"], s["signal_index"]): s for s in read(f"{d}/signals.csv")}
        for r in read(f"{d}/rounds.csv"):
            trades.append(dict(sym=r["symbol"], day=day, r=r, sig=sig.get((r["symbol"], r["signal_index"]))))
else:
    for m in range(1, 10):
        mo = f"2026-{m:02d}"
        cl = json.load(open(f"/data/p12r3a/closes-sym-b1-cap0-{mo}.json"))["t-bid-btc4h-q1@" + B1F][mo]["0"]
        keys = defaultdict(list)
        for ms, usd, sym in cl:
            keys[(sym, ms)].append(usd)
        for p in sorted(glob.glob(f"/data/p12r3a/b1-b/{mo}/*/t-bid-btc4h-q1/rounds.csv")):
            day = p.split("/")[-3]
            for r in read(p):
                if r["form"] != B1F or not isA(r["symbol"]):
                    continue
                net = float(r["net_bps"]); k = float(r["qty"]) * float(r["entry_px"]) / 1e4
                ms = int(r["exit_ns"]) // 1000000 if r["exit_ns"] else 0
                if not any(abs(u - net * k) < 0.01 for d in (0, -1, 1) for u in keys.get((r["symbol"], ms + d), [])):
                    continue   # сделка не вошла в psim cap0
                trades.append(dict(sym=r["symbol"], day=day, r=r, sig=None))
days = sorted({t["day"] for t in trades})
if LIMIT:
    keep = set(days[:: max(1, len(days) // LIMIT)][:LIMIT]); trades = [t for t in trades if t["day"] in keep]
print(f"{MODE}: {len(trades)} сделок, {len({t['day'] for t in trades})} суток", flush=True)

# --- признаки join: sigma/regime по сделкам, файл читается один раз и освобождается --------------------
def asof(xs, vs, t):
    i = bisect.bisect_right(xs, t) - 1
    return vs[i] if i >= 0 else ""


def fill_static(trades):
    """sigma240 и btc4h-режим на t0 сигнала: сделки группируются по файлу, файл читается и сразу отпускается
    (прежний кэш держал sigma-файлы всех монет за все месяцы -> OOM 8 ГБ, TK-327)."""
    by_sig, by_reg = defaultdict(list), defaultdict(list)
    for t in trades:
        t["t0"] = int(t["r"]["t0_ns"]) // 1_000_000
        by_sig[f"{paths(t['day'])['sigma']}/sigma-{t['sym']}.csv"].append(t)
        by_reg[paths(t["day"])["regime"]].append(t)
    for p, ts_ in by_sig.items():
        xs, vs = [], []
        if os.path.exists(p):
            with open(p, newline="") as f:
                for r in csv.DictReader(l for l in f if not l.startswith("#")):
                    xs.append(int(r["window_end_ms"])); vs.append(r["sigma_bps"])
        for t in ts_:
            t["sigma240"] = asof(xs, vs, t["t0"])
    for p, ts_ in by_reg.items():
        try:
            rows = read(p); xs = [int(r["minute_ms"]) for r in rows]; vs = [r["btc_ret_4h_bps"] for r in rows]
        except Exception:
            xs, vs = [], []
        for t in ts_:
            t["regime"] = asof(xs, vs, t["t0"])


def appr_for(day, sym, need):
    """approaches bid-стороны: только строки с arm_ms/touch_start_ms из набора t0 сделок группы (без кэша)."""
    p = f"{paths(day)['appr']}/approaches-{sym}.csv"
    by = defaultdict(list)
    if os.path.exists(p):
        for a in read(p):
            if a["side"] != "bid":
                continue
            for k in {int(a["arm_ms"]), int(a["touch_start_ms"]) if a["touch_start_ms"] else -1}:
                if k in need:
                    by[k].append(a)
    return by


# --- лента: цены после входа ---------------------------------------------
def tape(day, sym, tick, lo_ms, hi_ms):
    """сделки ленты только в окне [lo_ms, hi_ms] (объединение окон сделок группы), в компактных массивах."""
    pth = paths(day)
    tmp = tempfile.mktemp(prefix="tk327-", suffix=".csv", dir=os.environ.get("TK327_TMP", "/tmp"))
    try:
        subprocess.run([BIN, "lob", "trades", "--root", pth["root"], "--symbol", sym, "--day", day, "--out", tmp],
                       check=True, capture_output=True, text=True)
        ts, px, sell = array("q"), array("q"), bytearray()
        with open(tmp, newline="") as f:
            rd = csv.reader(f)
            h = {c: i for i, c in enumerate(next(rd))}
            it, ip, ipu, isd = h["exch_ts_ms"], h["price_tick"], h["price_usd"], h["side"]
            first = True
            tend = 0   # время последней сделки ленты суток (или первой за окном): покрытие для «цена через Δ»
            for r in rd:
                if first:
                    first = False
                    if abs(float(r[ipu]) - int(r[ip]) * tick) > 1e-6 * float(r[ipu]):
                        raise ValueError(f"tick {tick} != price_usd/price_tick")
                t = int(r[it])
                tend = t
                if t < lo_ms:
                    continue
                if t > hi_ms:
                    break
                ts.append(t); px.append(int(r[ip])); sell.append(r[isd] == "sell")
        return ts, px, sell, tend
    finally:
        if os.path.exists(tmp):
            os.remove(tmp)


_grid = {}


def grid(day, sym):
    """(tick, lot) бинлога суток из его заголовка: тик/лот бинлога (гребёнка архива) НЕ равны instruments.csv
    (BCH: 0.1 против 0.01), а price_tick/size_at_arm/qty_lots — в единицах бинлога."""
    k = (day, sym)
    if k not in _grid:
        with open(f"{paths(day)['root']}/{sym}-{day}.binlog", "rb") as f:
            h = f.read(21)
        t9, s9 = struct.unpack("<qq", h[5:21])
        _grid[k] = (t9 / 1e9, s9 / 1e9)
    return _grid[k]


def one_group(item):
    (day, sym), rows = item
    try:
        tick, lot = grid(day, sym)
        lo = min(t["t0"] for t in rows)
        hi = max((int(t["r"]["exit_ns"]) // 1_000_000 if t["r"]["exit_ns"] else 1 << 60) for t in rows) + max(DELTAS) * 1000
        ts, px, sell, tend = tape(day, sym, tick, lo, hi)
        err = ""
    except Exception as e:   # нет ленты -> признаки цены пусты, но join-признаки остаются
        ts, px, sell, tend, tick, lot, err = [], [], [], 0, 0.0, 0.0, f"{type(e).__name__}:{str(e)[:80]}"
    need = {t["t0"] for t in rows}
    ap = appr_for(day, sym, need)
    out = []
    for t in rows:
        r = t["r"]; t0 = int(r["t0_ns"]) // 1_000_000; ent = float(r["entry_px"]); dr = int(r["dir"])
        o = dict(mode=MODE, sym=sym, day=day, mon=day[:7], signal_index=r["signal_index"], t0_ns=r["t0_ns"], dir=dr,
                 reason=r["reason"], net_bps=r["net_bps"], entry_px=r["entry_px"], qty=r["qty"], exit_ns=r["exit_ns"],
                 tape_err=err)
        # признаки из join
        cand = ap.get(t0, [])
        a = None
        if t["sig"] and len(cand) > 1:
            cand = [c for c in cand if c["price_tick"] == t["sig"]["price_tick"]] or cand
        if cand:
            a = cand[0]
        o["appr_n"] = len(cand)
        o["wall_usd"] = round(int(a["price_tick"]) * tick * int(a["size_at_arm"]) * lot, 1) if a and tick > 0 else ""
        if a:
            fl = int(a["flow_1h_lots"])
            o["flow_pct"] = round(int(a["size_at_arm"]) / fl * 100, 4) if fl > 0 else ""
            for w in ("10", "20", "50"):
                o[f"strength_w{w}_pct"] = a[f"strength_w{w}_pct"]
            o["arm_dist_bps"] = a["arm_dist_bps"]; o["age_ms"] = a["age_ms"]
        else:
            o["flow_pct"] = o["strength_w10_pct"] = o["strength_w20_pct"] = o["strength_w50_pct"] = o["arm_dist_bps"] = o["age_ms"] = ""
        o["regime_btc4h"] = t["regime"]; o["sigma240"] = t["sigma240"]
        # цена после входа
        o["fill_lag_s"] = o["ret1_bps"] = o["ret5_bps"] = o["ret30_bps"] = ""
        if ts and tick > 0:
            thr = ent / tick   # в тиках: сделка-продажа с ценой <= entry_px
            i = bisect.bisect_left(ts, t0)
            exit_ms = int(r["exit_ns"]) // 1_000_000 if r["exit_ns"] else tend
            while i < len(ts) and ts[i] <= exit_ms and not (sell[i] and px[i] <= thr + 1e-9):
                i += 1
            if i < len(ts) and ts[i] <= exit_ms:
                tin = ts[i]
                o["fill_lag_s"] = round((tin - t0) / 1000, 3)
                for dl in DELTAS:
                    j = bisect.bisect_right(ts, tin + dl * 1000) - 1
                    if tend >= tin + dl * 1000 and j >= 0:
                        o[f"ret{dl}_bps"] = round((px[j] * tick - ent) / ent * 1e4 * dr, 3)
        out.append(o)
    return out


groups = defaultdict(list)
for t in trades:
    groups[(t["day"], t["sym"])].append(t)
fill_static(trades)
res = []
with ThreadPoolExecutor(WORKERS) as ex:
    for n, o in enumerate(ex.map(one_group, sorted(groups.items()))):
        res.extend(o)
        if n % 200 == 0:
            print(f"  групп {n}/{len(groups)}", flush=True)
cols = list(res[0].keys())
with open(OUT + ".tmp", "w", newline="") as f:
    w = csv.DictWriter(f, cols); w.writeheader(); w.writerows(res)
os.replace(OUT + ".tmp", OUT)
n = len(res)
cov = lambda k: sum(1 for r in res if r[k] != "")
print(f"{MODE}: n={n} wall_usd {cov('wall_usd')} flow_pct {cov('flow_pct')} regime {cov('regime_btc4h')} sigma {cov('sigma240')} "
      f"fill {cov('fill_lag_s')} ret1 {cov('ret1_bps')} ret5 {cov('ret5_bps')} ret30 {cov('ret30_bps')} tape_err {sum(1 for r in res if r['tape_err'])}")
