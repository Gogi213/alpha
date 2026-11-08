"""П-40 G0: сверка часов бирж — кросс-корреляция приращений mid DOGE (Binance, OKX из эталона TK-501 book_*.npz; Bybit ob200 с бокса), сутки 17.09.
Запуск (5.231.25.224): g0_clocks.py <каталог p40ref/out> <выход.json>. Сетка 100 мс (Bybit ts на ней), приращения mid за 100 мс, лаг ±3 с шагом 100 мс;
оценка смещения = argmax корреляции + параболическая подгонка; по часам (UTC) 24 оценки -> медиана, размах. Знак: lag>0 = второй ряд запаздывает относительно первого."""
import sys, json, subprocess, zipfile, os, shutil
import numpy as np
try:
    import orjson; loads = orjson.loads
except ImportError:
    loads = json.loads
OUT, RES = sys.argv[1], sys.argv[2]
T0 = 1789603200000          # 2026-09-17 00:00 UTC, мс
G = 100; N = 864000
RSH = "ssh -p 23 -i /root/.ssh/id_box -c aes128-gcm@openssh.com -o Compression=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null"
BOX = "u677479@u677479.your-storagebox.de"

def grid(t, mid):
    """последнее значение mid на сетке G мс (по порядку записи, t должно быть неубывающим -> берём максимум префикса)."""
    t = np.maximum.accumulate(t)
    idx = (t - T0) // G
    ok = (idx >= 0) & (idx < N)
    g = np.full(N, np.nan)
    g[idx[ok]] = mid[ok]            # последняя запись в ячейке выигрывает (порядок записи)
    s = np.isnan(g)
    i = np.where(~s, np.arange(N), 0); np.maximum.accumulate(i, out=i)
    g = g[i]; g[:np.argmax(~s)] = np.nan
    return g

def from_npz(e):
    z = np.load(f"{OUT}/book_{e}.npz"); o = z["o"]
    return grid(z["t"], (o[:, 4] + o[:, 5]) / 2)

def bybit():
    d = "/dev/shm/p40clk"; os.makedirs(d, exist_ok=True)
    fn = "DOGEUSDT_2026-09-17_ob200.data.zip"
    subprocess.run(["rsync", "-a", "-e", RSH, f"{BOX}:alpha/stakan/bybit-linear-ob200/2026-09-17/{fn}", d + "/"], check=True)
    f = zipfile.ZipFile(f"{d}/{fn}"); f = f.open(f.namelist()[0])
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
    shutil.rmtree(d, ignore_errors=True)
    return grid(np.array(ts), np.array(mid))

def xcorr(a, b, lags):
    """corr(da[t], db[t+L]) для L в lags (в ячейках), по приращениям; NaN исключены."""
    da = np.diff(a); db = np.diff(b)
    out = []
    for L in lags:
        x = da[max(0, -L):len(da) - max(0, L)]; y = db[max(0, L):len(db) - max(0, -L)]
        k = np.isfinite(x) & np.isfinite(y)
        out.append(float(np.corrcoef(x[k], y[k])[0, 1]) if k.sum() > 1000 else float("nan"))
    return np.array(out)

def est(a, b, lags):
    c = xcorr(a, b, lags); j = int(np.nanargmax(c))
    if 0 < j < len(c) - 1 and np.isfinite(c[j - 1]) and np.isfinite(c[j + 1]):
        den = c[j - 1] - 2 * c[j] + c[j + 1]
        fr = 0.5 * (c[j - 1] - c[j + 1]) / den if den != 0 else 0.0
    else:
        fr = 0.0
    return (lags[j] + fr) * G, float(c[j]), c

ser = {"binance": from_npz("binance"), "okx": from_npz("okx"), "bybit": bybit()}
lags = list(range(-30, 31))
res = {}
for (x, y) in (("binance", "okx"), ("binance", "bybit"), ("okx", "bybit")):
    a, b = ser[x], ser[y]
    r = {}
    m, cmax, c = est(a, b, lags)
    r["all_day_ms"] = m; r["corr_max"] = cmax; r["corr_lag0"] = float(c[30])
    hrs = []
    for h in range(24):
        s = slice(h * 36000, (h + 1) * 36000)
        mh, ch, _ = est(a[s], b[s], lags)
        hrs.append((mh, ch))
    r["hours_ms"] = [round(v[0], 1) for v in hrs]; r["hours_corr"] = [round(v[1], 3) for v in hrs]
    r["hours_median_ms"] = float(np.median([v[0] for v in hrs])); r["hours_min_ms"] = min(v[0] for v in hrs); r["hours_max_ms"] = max(v[0] for v in hrs)
    res[f"{x}->{y}"] = r
    print(x, y, r["all_day_ms"], r["corr_max"], r["hours_median_ms"], r["hours_min_ms"], r["hours_max_ms"], flush=True)
json.dump(res, open(RES, "w"), indent=1)
