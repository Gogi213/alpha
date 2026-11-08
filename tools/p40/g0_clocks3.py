"""П-40 G0 (TK-496 возврат п.4): часы бирж — смещение рядов mid по кросс-корреляции приращений на сетке 100 мс.
Биржи: Binance, OKX, Bitget, Aster, Bybit, Gate (Hyperliquid — только снимки раз в ~5 с, часы не оцениваются).
Активы DOGE, LINK, AVAX (есть у всех шести; BTC/ETH в пулах CHFT нет); сутки 18.09, 25.09, 14.10, 21.10 (разные недели, вне дыры Gate 29.09–08.10);
часы UTC 02, 08, 14, 20. Единица = (актив, сутки): 6 рядов × 4 часа -> 15 пар × 4 часа.
Время ряда: event_time биржи (CHFT), ts (Bybit), ts (Gate). Знак: lag>0 = второй ряд запаздывает относительно первого.
Запуск (5.231.25.224, юниты под nice): g0_clocks3.py <выход.jsonl> [процессов]. Возобновляемо; метка <выход>.done. Исходов не касается."""
import sys, os, json, subprocess, shutil, zipfile, gzip, itertools, datetime as dt
from multiprocessing import Pool
import numpy as np
import pyarrow as pa, pyarrow.parquet as pq
from numba import njit, types
from numba.typed import Dict
try:
    import orjson; loads = orjson.loads
except ImportError:
    loads = json.loads

BOX = "u677479@u677479.your-storagebox.de"
RSH = "ssh -p 23 -i /root/.ssh/id_box -c aes128-gcm@openssh.com -o Compression=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -q"
TMP = "/dev/shm/p40clk3"
ASSETS = ["DOGE", "LINK", "AVAX"]
DAYS = ["2026-09-18", "2026-09-25", "2026-10-14", "2026-10-21"]
HOURS = [2, 8, 14, 20]
EXS = ["binance", "okx", "bitget", "aster", "bybit", "gate"]
G = 100; N = 36000; LAGS = list(range(-30, 31))

def chft_path(ex, a, day, hh):
    name = {"okx": f"{a}-USDT-SWAP", "binance": f"{a}USDT", "bitget": f"{a}USDT", "aster": f"{a}USDT"}[ex]
    return f"chft/{ex}_futures/{day}/{hh:02d}/{name}_orderbook.parquet"

@njit(cache=True)
def _mid(is_snap, is_bid, price, qty, fid, ev, tcode):
    n = len(price)
    bids = Dict.empty(types.float64, types.float64); asks = Dict.empty(types.float64, types.float64)
    nb = -1e300; na = 1e300; prev_snap = False
    ot = np.empty(n); om = np.empty(n); k = 0
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
                ot[k] = ev[i]; om[k] = 0.5 * (nb + na); k += 1
    return ot[:k], om[:k]

def chft_series(path):
    t = pq.read_table(path)
    et_d = t["event_type"].dictionary_encode().combine_chunks()
    tcode = et_d.indices.to_numpy(zero_copy_only=False).astype(np.int64); names = et_d.dictionary.to_pylist()
    is_snap = tcode == (names.index("snapshot") if "snapshot" in names else -1)
    ev = t["event_time"].to_numpy().astype(float)
    fid = t["final_update_id"].to_numpy(zero_copy_only=False).astype(float)
    sd = t["side"].dictionary_encode().combine_chunks(); sn = sd.dictionary.to_pylist()
    is_bid = sd.indices.to_numpy(zero_copy_only=False) == (sn.index("bid") if "bid" in sn else -1)
    price = t["price"].cast(pa.float64()).to_numpy(zero_copy_only=False); qty = t["quantity"].cast(pa.float64()).to_numpy(zero_copy_only=False)
    return _mid(is_snap, is_bid, price, qty, fid, ev, tcode)

@njit(cache=True)
def _gate_kern(ev, pi, s, up):
    n = len(ev); nup = len(up)
    L = np.zeros(nup); bi = -1; ai = nup
    ot = np.empty(n); om = np.empty(n); k = 0
    # t подаётся отдельно: храним индексы событий, время подставляется снаружи
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
            ot[k] = i; om[k] = 0.5 * (up[bi] + up[ai]); k += 1
    return ot[:k], om[:k]

def gate_series_t(path):
    import pandas as pd
    df = pd.read_csv(path, header=None, names=["t", "e", "p", "s", "i", "f"], usecols=[0, 1, 2, 3], dtype={"t": "float64", "e": "category", "p": "float64", "s": "float64"})
    ev = df["e"].astype(str).map({"set": 0, "make": 1, "take": 2}).to_numpy(np.int8)
    t = df["t"].to_numpy(); p = df["p"].to_numpy(); s = df["s"].to_numpy()
    up, inv = np.unique(p, return_inverse=True)
    idx, om = _gate_kern(ev, inv.astype(np.int64), s, up)
    return t[idx.astype(np.int64)] * 1000.0, om

def bybit_day(path):
    """сутки целиком: (ts, mid) по сообщениям; ts в мс."""
    f = zipfile.ZipFile(path); f = f.open(f.namelist()[0])
    ts, mid = [], []; bids = {}; asks = {}; bb = 0.0; ba = float("inf")
    for line in f:
        m = loads(line); dd = m["data"]
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
            ts.append(m["ts"]); mid.append((bb + ba) / 2)
    return np.array(ts, dtype=float), np.array(mid)

def grid(t, mid, t0):
    t = np.maximum.accumulate(t)
    idx = ((t - t0) // G).astype(np.int64)
    ok = (idx >= 0) & (idx < N)
    g = np.full(N, np.nan)
    if not ok.any(): return g
    g[idx[ok]] = mid[ok]
    # значение до первой записи часа — последнее до часа (если есть)
    s = np.isnan(g)
    i = np.where(~s, np.arange(N), 0); np.maximum.accumulate(i, out=i)
    g = g[i]; g[:np.argmax(~s)] = np.nan
    return g

def xcorr(a, b):
    da = np.diff(a); db = np.diff(b); out = []
    for L in LAGS:
        x = da[max(0, -L):len(da) - max(0, L)]; y = db[max(0, L):len(db) - max(0, -L)]
        k = np.isfinite(x) & np.isfinite(y)
        out.append(float(np.corrcoef(x[k], y[k])[0, 1]) if k.sum() > 1000 and x[k].std() > 0 and y[k].std() > 0 else float("nan"))
    return np.array(out)

def est(a, b):
    c = xcorr(a, b)
    if not np.isfinite(c).any(): return None
    j = int(np.nanargmax(c))
    fr = 0.0
    if 0 < j < len(c) - 1 and np.isfinite(c[j - 1]) and np.isfinite(c[j + 1]):
        den = c[j - 1] - 2 * c[j] + c[j + 1]
        fr = 0.5 * (c[j - 1] - c[j + 1]) / den if den != 0 else 0.0
    return (LAGS[j] + fr) * G, float(c[j]), float(c[LAGS.index(0)])

def fetch(rel, dst):
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    for _ in range(3):
        r = subprocess.run(["rsync", "-a", "-e", RSH, f"{BOX}:alpha/stakan/{rel}", dst], capture_output=True, text=True)
        if r.returncode == 0: return True
    return False

def unit(u):
    a, day = u
    d = f"{TMP}/{a}_{day}"; shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc)
    res = {"asset": a, "day": day, "pairs": [], "missing": []}
    ser = {h: {} for h in HOURS}
    try:
        for h in HOURS:
            t0 = (d0 + dt.timedelta(hours=h)).timestamp() * 1000
            for ex in ("binance", "okx", "bitget", "aster"):
                try:
                    if ex == "binance":
                        pth = f"/data/stakan/{chft_path(ex, a, day, h)}"
                    else:
                        pth = f"{d}/{ex}_{h}.parquet"
                        if not fetch(chft_path(ex, a, day, h), pth): raise FileNotFoundError(ex)
                    t, m = chft_series(pth)
                    ser[h][ex] = grid(t, m, t0)
                except Exception as e:
                    res["missing"].append([ex, h, repr(e)[:80]])
            try:
                pth = f"{d}/gate_{h}.csv.gz"
                if not fetch(f"gate-futures/{a}_USDT/{a}_USDT-{day.replace('-', '')}{h:02d}.csv.gz", pth): raise FileNotFoundError("gate")
                t, m = gate_series_t(pth)
                ser[h]["gate"] = grid(t, m, t0)
            except Exception as e:
                res["missing"].append(["gate", h, repr(e)[:80]])
        zp = f"{d}/bybit.zip"
        if fetch(f"bybit-linear-ob200/{day}/{a}USDT_{day}_ob200.data.zip", zp):
            t, m = bybit_day(zp)
            for h in HOURS:
                t0 = (d0 + dt.timedelta(hours=h)).timestamp() * 1000
                # значение до начала часа: берём весь ряд, ячейки до первой записи часа заполняются последней прежней
                ser[h]["bybit"] = grid(t, m, t0)
        else:
            res["missing"].append(["bybit", -1, "fetch"])
        for h in HOURS:
            for x, y in itertools.combinations(EXS, 2):
                if x in ser[h] and y in ser[h]:
                    e = est(ser[h][x], ser[h][y])
                    if e: res["pairs"].append({"x": x, "y": y, "h": h, "ms": e[0], "corr": e[1], "corr0": e[2]})
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return res

if __name__ == "__main__":
    out = sys.argv[1]; nproc = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    done = set()
    if os.path.exists(out):
        for l in open(out):
            try: r = json.loads(l); done.add((r["asset"], r["day"]))
            except Exception: pass
    todo = [(a, d) for d in DAYS for a in ASSETS if (a, d) not in done]
    with Pool(nproc) as pool, open(out, "a") as f:
        for r in pool.imap_unordered(unit, todo):
            f.write(json.dumps(r) + "\n"); f.flush(); print(r["asset"], r["day"], len(r["pairs"]), r["missing"][:3], flush=True)
    open(out + ".done", "w").write("ok")
