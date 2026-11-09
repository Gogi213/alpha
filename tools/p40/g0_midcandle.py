"""П-40 G0 (TK-496 возврат 2, п.1): сверка книги со свечами 1 м: отклонение mid в конце минуты от close свечи (bps) и попадание close в [min bid, max ask] книги за минуту.
Биржи: Binance, OKX, Bitget, Aster, Bybit (публичный REST свечей); Gate — свечей за 17.09–22.10 REST не отдаёт (HTTP 400 «Candlestick too long ago», URL в отчёте) ->
замена по решению Судьи: цены сделок Gate (события `take`) против [bid, ask] книги и отклонение последней сделки минуты от mid конца минуты.
Выборка как в g0_clocks3: DOGE/LINK/AVAX × 18.09, 25.09, 14.10, 21.10 × часы 02/08/14/20 UTC (60 минут). Допуск = 3·MAD отклонений по бирже. Исходов не касается.
Запуск: g0_midcandle.py <выход.json> [процессов]."""
import sys, os, json, shutil, zipfile, urllib.request, datetime as dt
from multiprocessing import Pool
import numpy as np, pyarrow as pa, pyarrow.parquet as pq
from numba import njit, types
from numba.typed import Dict
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g0_clocks3 as C

TMP = "/dev/shm/p40mc"
EXS = ["binance", "okx", "bitget", "aster", "bybit", "gate"]
URLS = {
    "bybit": "https://api.bybit.com/v5/market/kline?category=linear&symbol={a}USDT&interval=1&start={s}&end={e}&limit=100",
    "binance": "https://fapi.binance.com/fapi/v1/klines?symbol={a}USDT&interval=1m&startTime={s}&endTime={e}&limit=100",
    "aster": "https://fapi.asterdex.com/fapi/v1/klines?symbol={a}USDT&interval=1m&startTime={s}&endTime={e}&limit=100",
    "okx": "https://www.okx.com/api/v5/market/history-candles?instId={a}-USDT-SWAP&bar=1m&after={e1}&limit=100",
    "bitget": "https://api.bitget.com/api/v2/mix/market/history-candles?symbol={a}USDT&productType=USDT-FUTURES&granularity=1m&startTime={s}&endTime={e}&limit=100",
}

def candles(ex, a, t0):
    """60 свечей часа: {open_ms: (low, high, close)}."""
    s = int(t0); e = s + 59 * 60000
    url = URLS[ex].format(a=a, s=s, e=e, e1=s + 3600000)
    for _ in range(4):
        try:
            raw = json.loads(urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "p40-g0"}), timeout=20).read())
            break
        except Exception as ex_:
            err = repr(ex_)[:80]; import time; time.sleep(1.5)
    else:
        return None, url + " " + err
    rows = raw["result"]["list"] if ex == "bybit" else raw["data"] if isinstance(raw, dict) else raw
    out = {}
    for r in rows:
        ts = int(r[0])
        if s <= ts <= e: out[ts] = (float(r[3]), float(r[2]), float(r[4]))
    return out, url

@njit(cache=True)
def _bbo(is_snap, is_bid, price, qty, fid, ev, tcode):
    n = len(price)
    bids = Dict.empty(types.float64, types.float64); asks = Dict.empty(types.float64, types.float64)
    nb = -1e300; na = 1e300; prev_snap = False
    ot = np.empty(n); ob = np.empty(n); oa = np.empty(n); k = 0
    for i in range(n):
        s = is_snap[i]
        if s and ((not prev_snap) or fid[i] != fid[i - 1]):
            bids = Dict.empty(types.float64, types.float64); asks = Dict.empty(types.float64, types.float64)
            nb = -1e300; na = 1e300
        prev_snap = s
        p = price[i]
        if is_bid[i]:
            if qty[i] == 0:
                if p in bids:
                    del bids[p]
                    if p == nb:
                        nb = -1e300
                        for q in bids:
                            if q > nb: nb = q
            else:
                bids[p] = qty[i]
                if p > nb: nb = p
        else:
            if qty[i] == 0:
                if p in asks:
                    del asks[p]
                    if p == na:
                        na = 1e300
                        for q in asks:
                            if q < na: na = q
            else:
                asks[p] = qty[i]
                if p < na: na = p
        if i + 1 == n or fid[i + 1] != fid[i] or tcode[i + 1] != tcode[i]:
            if len(bids) > 0 and len(asks) > 0 and nb < na:
                ot[k] = ev[i]; ob[k] = nb; oa[k] = na; k += 1
    return ot[:k], ob[:k], oa[:k]

def chft_bbo(path):
    t = pq.read_table(path)
    et_d = t["event_type"].dictionary_encode().combine_chunks()
    tcode = et_d.indices.to_numpy(zero_copy_only=False).astype(np.int64); names = et_d.dictionary.to_pylist()
    is_snap = tcode == (names.index("snapshot") if "snapshot" in names else -1)
    ev = t["event_time"].to_numpy().astype(float)
    fid = t["final_update_id"].to_numpy(zero_copy_only=False).astype(float)
    sd = t["side"].dictionary_encode().combine_chunks(); sn = sd.dictionary.to_pylist()
    is_bid = sd.indices.to_numpy(zero_copy_only=False) == (sn.index("bid") if "bid" in sn else -1)
    price = t["price"].cast(pa.float64()).to_numpy(zero_copy_only=False); qty = t["quantity"].cast(pa.float64()).to_numpy(zero_copy_only=False)
    return _bbo(is_snap, is_bid, price, qty, fid, ev, tcode)

def bybit_bbo(path):
    f = zipfile.ZipFile(path); f = f.open(f.namelist()[0])
    ts, bb_l, ba_l = [], [], []; bids = {}; asks = {}; bb = 0.0; ba = float("inf")
    for line in f:
        m = C.loads(line); dd = m["data"]
        if m["type"] == "snapshot": bids.clear(); asks.clear(); bb = 0.0; ba = float("inf")
        for p, q in dd["b"]:
            pf = float(p)
            if float(q) == 0:
                if p in bids:
                    del bids[p]
                    if pf == bb: bb = max(map(float, bids)) if bids else 0.0
            else:
                bids[p] = q
                if pf > bb: bb = pf
        for p, q in dd["a"]:
            pf = float(p)
            if float(q) == 0:
                if p in asks:
                    del asks[p]
                    if pf == ba: ba = min(map(float, asks)) if asks else float("inf")
            else:
                asks[p] = q
                if pf < ba: ba = pf
        if bids and asks and bb < ba:
            ts.append(m["ts"]); bb_l.append(bb); ba_l.append(ba)
    return np.array(ts, dtype=float), np.array(bb_l), np.array(ba_l)

@njit(cache=True)
def _gate_bbo(ev, pi, s, up):
    n = len(ev); nup = len(up)
    L = np.zeros(nup); bi = -1; ai = nup
    oi = np.empty(n, np.int64); ob = np.empty(n); oa = np.empty(n); k = 0
    for i in range(n):
        j = pi[i]
        if ev[i] == 0: new = s[i]
        elif ev[i] == 1: new = L[j] + s[i]
        else: new = L[j] - s[i]
        if abs(new) < 1e-12: new = 0.0
        L[j] = new
        if new > 0:
            if j > bi: bi = j
        elif j == bi:
            bi = -1
            for x in range(j - 1, -1, -1):
                if L[x] > 0:
                    bi = x; break
        if new < 0:
            if j < ai: ai = j
        elif j == ai:
            ai = nup
            for x in range(j + 1, nup):
                if L[x] < 0:
                    ai = x; break
        if bi >= 0 and ai < nup and bi < ai:
            oi[k] = i; ob[k] = up[bi]; oa[k] = up[ai]; k += 1
    return oi[:k], ob[:k], oa[:k]

def gate_bbo(path):
    import pandas as pd
    df = pd.read_csv(path, header=None, names=["t", "e", "p", "s", "i", "f"], usecols=[0, 1, 2, 3], dtype={"t": "float64", "e": "category", "p": "float64", "s": "float64"})
    ev = df["e"].astype(str).map({"set": 0, "make": 1, "take": 2}).to_numpy(np.int8)
    t = df["t"].to_numpy() * 1000.0; p = df["p"].to_numpy(); s = df["s"].to_numpy()
    up, inv = np.unique(p, return_inverse=True)
    idx, ob, oa = _gate_bbo(ev, inv.astype(np.int64), s, up)
    tk = ev == 2
    return t[idx], ob, oa, t[tk], p[tk]

def minutes(t, bid, ask, t0):
    """по 60 минутам часа: (mid_конца, min_bid, max_ask) или None; состояние переносится вперёд."""
    t = np.maximum.accumulate(t); out = []
    for m in range(60):
        a = t0 + m * 60000; b = a + 60000
        i1 = np.searchsorted(t, b, side="left")      # состояния с t < b
        i0 = np.searchsorted(t, a, side="left")      # состояния с t < a
        if i1 == 0: out.append(None); continue
        lo = max(i0 - 1, 0) if i0 > 0 else i0         # перенос последнего состояния до минуты
        sl = slice(lo, i1)
        out.append(((bid[i1 - 1] + ask[i1 - 1]) / 2, float(bid[sl].min()), float(ask[sl].max())))
    return out

def unit(u):
    ex, a, day = u
    d = f"{TMP}/{ex}_{a}_{day}"; shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    res = []
    try:
        if ex == "bybit":
            zp = f"{d}/bybit.zip"
            if not C.fetch(f"bybit-linear-ob200/{day}/{a}USDT_{day}_ob200.data.zip", zp): return [{"ex": ex, "asset": a, "day": day, "err": "fetch"}]
            bt, bb, ba = bybit_bbo(zp)
        for h in C.HOURS:
            t0 = (d0 + dt.timedelta(hours=h)).timestamp() * 1000
            r = {"ex": ex, "asset": a, "day": day, "h": h}
            if ex == "gate":
                pth = f"{d}/gate_{h}.csv.gz"
                if not C.fetch(f"gate-futures/{a}_USDT/{a}_USDT-{day.replace('-', '')}{h:02d}.csv.gz", pth): r["err"] = "fetch"; res.append(r); continue
                t, bb_, ba_, tt, tp = gate_bbo(pth)
                mm = minutes(t, bb_, ba_, t0)
                inside = 0; n = 0; dev = []; lo_out = []
                for m in range(60):
                    if mm[m] is None: continue
                    sel = (tt >= t0 + m * 60000) & (tt < t0 + (m + 1) * 60000)
                    if not sel.any(): continue
                    px = tp[sel]; n += len(px); inside += int(((px >= mm[m][1] - 1e-12) & (px <= mm[m][2] + 1e-12)).sum())
                    dev.append((px[-1] - mm[m][0]) / mm[m][0] * 1e4)
                r.update({"n_trades": n, "trades_inside": inside, "dev": dev})
            else:
                if ex == "bybit": bp = None
                elif ex == "binance": bp = f"/data/stakan/{C.chft_path(ex, a, day, h)}"
                else:
                    bp = f"{d}/ob_{h}.parquet"
                    if not C.fetch(C.chft_path(ex, a, day, h), bp): r["err"] = "fetch"; res.append(r); continue
                if ex != "bybit": t, bb, ba = chft_bbo(bp)
                cd, url = candles(ex, a, t0)
                if cd is None: r["err"] = "candles " + url; res.append(r); continue
                mm = minutes(bt if ex == "bybit" else t, bb, ba, t0)
                dev = []; inside = 0; n = 0
                for m in range(60):
                    c = cd.get(int(t0 + m * 60000))
                    if c is None or mm[m] is None: continue
                    dev.append((mm[m][0] - c[2]) / c[2] * 1e4); n += 1
                    inside += int(mm[m][1] - 1e-12 <= c[2] <= mm[m][2] + 1e-12)
                r.update({"n_min": n, "close_inside": inside, "dev": dev, "n_candles": len(cd)})
            res.append(r)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return res

if __name__ == "__main__":
    nproc = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    units = [(e, a, d) for e in EXS for a in C.ASSETS for d in C.DAYS]
    units.sort(key=lambda u: u[0] != "bybit")             # тяжёлый Bybit первым
    res = []
    with Pool(nproc) as pool:
        for r in pool.imap_unordered(unit, units): res += r
    summ = {}
    for e in EXS:
        rs = [r for r in res if r["ex"] == e]; ok = [r for r in rs if "dev" in r]
        dv = np.concatenate([np.array(r["dev"]) for r in ok]) if ok else np.array([])
        if not len(dv): summ[e] = {"hours": 0, "errs": [r.get("err") for r in rs if "err" in r][:3]}; continue
        med = float(np.median(dv)); mad = float(np.median(np.abs(dv - med)))
        s = {"hours": len(ok), "errs": len([r for r in rs if "err" in r]), "n": int(len(dv)), "median_bps": med, "MAD_bps": mad, "tol_3MAD_bps": 3 * mad,
             "q1_q99_bps": [float(x) for x in np.percentile(dv, [1, 99])], "share_outside_3MAD": float((np.abs(dv - med) > 3 * mad).mean())}
        if e == "gate": s["trades"] = int(sum(r["n_trades"] for r in ok)); s["trades_inside_share"] = sum(r["trades_inside"] for r in ok) / max(1, s["trades"])
        else: s["close_inside_share"] = sum(r["close_inside"] for r in ok) / max(1, sum(r["n_min"] for r in ok))
        summ[e] = s; print(e, s, flush=True)
    json.dump({"summary": summ, "hours": res}, open(sys.argv[1], "w"))
