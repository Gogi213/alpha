"""П-40 G0 (TK-496 возврат п.2): сверка mid книги с mark price биржи — отклонение (mid − mark)/mark в bps в моменты обновления mark.
Биржи с mark в CHFT: Binance, OKX, Bitget, Aster (у Bybit ob200 и Gate mark нет; свечи 17.09–22.10 на сервере отсутствуют -> сверка со свечами не делалась).
Выборка та же, что у g0_clocks3: активы DOGE/LINK/AVAX, сутки 18.09/25.09/14.10/21.10, часы 02/08/14/20 UTC. Допуск = 3·MAD распределения отклонений по бирже.
Запуск: g0_midmark.py <выход.json>. Исходов не касается."""
import sys, os, json, shutil, subprocess
from multiprocessing import Pool
import numpy as np, pyarrow as pa, pyarrow.parquet as pq
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g0_clocks3 as C

TMP = "/dev/shm/p40mm"

def unit(u):
    ex, a, day = u
    d = f"{TMP}/{ex}_{a}_{day}"; shutil.rmtree(d, ignore_errors=True); os.makedirs(d)
    out = []
    try:
        name = {"okx": f"{a}-USDT-SWAP"}.get(ex, f"{a}USDT")
        for h in C.HOURS:
            bp = f"{d}/ob_{h}.parquet"; mp = f"{d}/mk_{h}.parquet"
            if ex == "binance": bp = f"/data/stakan/{C.chft_path(ex, a, day, h)}"
            elif not C.fetch(C.chft_path(ex, a, day, h), bp): continue
            if not C.fetch(f"chft/{ex}_futures/{day}/{h:02d}/{name}_mark_price.parquet", mp): continue
            t, m = C.chft_series(bp)
            mk = pq.read_table(mp)
            mt = mk["event_time"].to_numpy().astype(float); mv = mk["mark_price"].cast(pa.float64()).to_numpy(zero_copy_only=False)
            i = np.searchsorted(t, mt, side="right") - 1
            ok = (i >= 0) & np.isfinite(mv) & (mv > 0)
            dev = (m[i[ok]] - mv[ok]) / mv[ok] * 1e4
            out.append({"ex": ex, "asset": a, "day": day, "h": h, "n": int(ok.sum()), "dev": [float(x) for x in np.percentile(dev, [1, 5, 25, 50, 75, 95, 99])], "dev_all": dev[::max(1, len(dev) // 2000)].tolist()})
    finally:
        shutil.rmtree(d, ignore_errors=True)
    return out

if __name__ == "__main__":
    res = []
    units = [(e, a, d) for e in ("binance", "okx", "bitget", "aster") for a in C.ASSETS for d in C.DAYS]
    with Pool(4) as pool:
        for r in pool.imap_unordered(unit, units): res += r
    summ = {}
    for e in ("binance", "okx", "bitget", "aster"):
        dv = np.concatenate([np.array(r["dev_all"]) for r in res if r["ex"] == e]); med = float(np.median(dv)); mad = float(np.median(np.abs(dv - med)))
        summ[e] = {"hours": sum(1 for r in res if r["ex"] == e), "n_marks": sum(r["n"] for r in res if r["ex"] == e), "median_bps": med, "MAD_bps": mad, "tol_3MAD_bps": 3 * mad,
                   "q1_q99_bps": [float(x) for x in np.percentile(dv, [1, 99])], "share_outside_3MAD": float((np.abs(dv - med) > 3 * mad).mean())}
        print(e, summ[e], flush=True)
    for r in res: r.pop("dev_all")
    json.dump({"summary": summ, "hours": res}, open(sys.argv[1], "w"), indent=1)
