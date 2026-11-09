"""П-40 G0 (TK-496 возврат 2, п.1): Gate — сверка mid книги со свечами 1 м из публичного архива Gate (download.gatedata.org/futures_usdt/candlesticks_1m/YYYYMM/<C>_USDT-YYYYMM.csv.gz;
REST свечей за эти даты не отдаёт: HTTP 400 «Candlestick too long ago»). Столбцы архива: t(с), объём, close, high, low, open. Та же выборка и метрики, что g0_midcandle.py.
Дополнительно — проверка `take` против книги (доля цен take внутри [bid, ask] состояния до события): показывает, что `take` Gate — не поток сделок.
Запуск: g0_gate_candle.py <выход.json> [процессов]. Исходов не касается."""
import sys, os, json, gzip, io, shutil, urllib.request, datetime as dt
from multiprocessing import Pool
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g0_clocks3 as C
import g0_midcandle as M

def month_candles(a, month):
    url = f"https://download.gatedata.org/futures_usdt/candlesticks_1m/{month}/{a}_USDT-{month}.csv.gz"
    raw = urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "p40-g0"}), timeout=60).read()
    out = {}
    for l in gzip.decompress(raw).decode().splitlines():
        p = l.split(",")
        out[int(p[0]) * 1000] = (float(p[4]), float(p[3]), float(p[2]))     # low, high, close
    return out, url

def unit(u):
    a, day = u
    d = f"{M.TMP}/gc_{a}_{day}"; shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    d0 = dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc); res = []
    try:
        cd, url = month_candles(a, day[:4] + day[5:7])
        for h in C.HOURS:
            t0 = (d0 + dt.timedelta(hours=h)).timestamp() * 1000
            r = {"ex": "gate", "asset": a, "day": day, "h": h, "url": url}
            pth = f"{d}/g.csv.gz"
            if not C.fetch(f"gate-futures/{a}_USDT/{a}_USDT-{day.replace('-', '')}{h:02d}.csv.gz", pth): r["err"] = "fetch"; res.append(r); continue
            t, bb, ba, tt, tp = M.gate_bbo(pth)
            if len(t) == 0: r["err"] = "no_diffs_hour"; res.append(r); continue
            mm = M.minutes(t, bb, ba, t0); dev = []; inside = 0; n = 0
            for m in range(60):
                c = cd.get(int(t0 + m * 60000))
                if c is None or mm[m] is None: continue
                dev.append((mm[m][0] - c[2]) / c[2] * 1e4); n += 1; inside += int(mm[m][1] - 1e-12 <= c[2] <= mm[m][2] + 1e-12)
            # take внутри [min bid, max ask] минуты
            tin = tn = 0
            for m in range(60):
                if mm[m] is None: continue
                sel = (tt >= t0 + m * 60000) & (tt < t0 + (m + 1) * 60000)
                if sel.any(): tn += int(sel.sum()); tin += int(((tp[sel] >= mm[m][1] - 1e-12) & (tp[sel] <= mm[m][2] + 1e-12)).sum())
            r.update({"n_min": n, "close_inside": inside, "dev": dev, "n_take": tn, "take_inside": tin}); res.append(r)
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return res

if __name__ == "__main__":
    res = []
    with Pool(int(sys.argv[2]) if len(sys.argv) > 2 else 6) as pool:
        for r in pool.imap_unordered(unit, [(a, d) for a in C.ASSETS for d in C.DAYS]): res += r
    ok = [r for r in res if "dev" in r]; dv = np.concatenate([np.array(r["dev"]) for r in ok])
    med = float(np.median(dv)); mad = float(np.median(np.abs(dv - med)))
    s = {"hours": len(ok), "errs": [(r["asset"], r["day"], r["h"], r["err"]) for r in res if "err" in r], "n": int(len(dv)), "median_bps": med, "MAD_bps": mad, "tol_3MAD_bps": 3 * mad,
         "q1_q99_bps": [float(x) for x in np.percentile(dv, [1, 99])], "share_outside_3MAD": float((np.abs(dv - med) > 3 * mad).mean()),
         "close_inside_share": sum(r["close_inside"] for r in ok) / sum(r["n_min"] for r in ok), "take_inside_share": sum(r["take_inside"] for r in ok) / sum(r["n_take"] for r in ok)}
    print(s); json.dump({"summary": s, "hours": res}, open(sys.argv[1], "w"))
