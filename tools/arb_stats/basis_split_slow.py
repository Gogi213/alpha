"""«Медленные» признаки пар по минутным таблицам basis_split_run (pkl): лестница масштабов, ACF, спектр, линии на периодах (фандинг), профили по часам суток,
амплитуда волны и выбросов по суткам для каждого окна опоры, активность вокруг моментов фандинга.

Запуск: python3 basis_split_slow.py <full.pkl> <выход.pkl> [PAR]. Выход — {"pairs": {ключ: признаки}, "meta": {...}}; на сырьё не возвращается.
Время — мс; сутки — GMT+4; моменты фандинга — 00:00 / 08:00 / 16:00 UTC (интервал по символам не записан — допущение, в отчёте).
"""
import pickle
import sys
from datetime import datetime, timezone
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import basis_split as bs  # noqa: E402
import basis_split_tools as bt  # noqa: E402

G = {}
TAIL_W = (20, 60, 240)                                                      # окна опоры для хвоста амплитуд
TAIL_ABS = 10.0 ** np.arange(-3, 1.01, 0.1)                                 # % (абсолютные границы корзин)
TAIL_G = 10.0 ** np.arange(-2, 1.51, 0.1)                                   # в единицах спреда пары G
TAIL_KINDS = ("sell", "buy", "mid")                                         # Sell − опора, опора − Buy, |середина − опора| (минутные экстремумы / медиана)
LINES = (1440, 960, 720, 480, 360, 240, 180, 120, 420, 540, 600, 300)      # периоды, мин: «настоящие» (24, 16, 12, 8, 6, 4, 3, 2 ч) и контрольные (7, 9, 10, 5 ч)
OFF = np.arange(-120, 181, 10)                                              # смещения от момента (мин) — левые границы 10-минутных корзин
FUND_H = (0, 8, 16)
PLACEBO_H = (2, 4, 6, 10, 12, 14, 18, 20, 22)


def utc_ms(y, mo, d, h=0):
    return int(datetime(y, mo, d, h, tzinfo=timezone.utc).timestamp() * 1000)


def event_times(t0, t1, hours):
    out = []
    day = utc_ms(2026, 11, 12)
    while day < t1:
        for h in hours:
            t = day + h * 3600_000
            if t0 + 130 * 60_000 <= t <= t1 - 190 * 60_000:     # профиль [−120; +190) мин должен целиком лежать в окне
                out.append(t)
        day += 86_400_000
    return out


def activity_profile(a, t0, times):
    """Нормированная активность a (|Δ минутной середины|) в 10-мин корзинах от −120 до +190 мин вокруг каждого момента: (моментов, корзин); NaN — нет данных."""
    out = np.full((len(times), len(OFF)), np.nan)
    for i, t in enumerate(times):
        k = (t - t0) // 60_000
        for j, o in enumerate(OFF):
            seg = a[k + o:k + o + 10]
            if np.isfinite(seg).sum() >= 5:
                out[i, j] = np.nanmean(seg)
    return out


def pair_slow(key):
    f = G["pairs"][key]
    t0, t1 = G["t0"], G["t1"]
    nday = len(G["days"]) - 1
    m = f["mins"]
    x = m["med"].astype(float)
    n = len(x)
    dom = f["day_of_min"].astype(int)
    L = f["ref_day"][dom]
    xt = x - L
    lvl = f["level_all"] if f["level_all"] is not None else np.nan
    refs = {w: bs.rolling_med_centred(x, w) for w in bt.LADDER_W}
    chain = [x] + [refs[w] for w in bt.LADDER_W] + [L, np.full(n, lvl)]
    comps = [chain[i] - chain[i + 1] for i in range(len(chain) - 1)]
    out = {"cover": float(np.isfinite(x).mean())}
    out["lad_std"] = np.array([np.nanstd(c) if np.isfinite(c).any() else np.nan for c in comps])
    out["lad_rng"] = np.array([np.nanpercentile(c, 95) - np.nanpercentile(c, 5) if np.isfinite(c).any() else np.nan for c in comps])
    out["tot_std"] = float(np.nanstd(x)) if np.isfinite(x).any() else np.nan
    # по суткам: амплитуда «медленной части выше окна W» = ref_W − ref_день; «быстрой» = минутная середина − ref_W; крайние отклонения минутных extremes от ref_W
    wave_std = np.full((nday, len(bt.LADDER_W)), np.nan)
    wave_rng = np.full((nday, len(bt.LADDER_W)), np.nan)
    fast_std = np.full((nday, len(bt.LADDER_W)), np.nan)
    ext = np.full((nday, len(bt.LADDER_W), 4), np.nan)        # p99 и макс отклонения extremes: вверх (hi − ref), вниз (ref − lo) середины
    sx = np.full((nday, len(bt.LADDER_W), 4), np.nan)         # p99 и макс: Sell − ref (smax), ref − Buy (bmin)
    hi, lo, smax, bmin = (m[k].astype(float) for k in ("hi", "lo", "smax", "bmin"))
    for d in range(nday):
        sel = dom == d
        for wi, w in enumerate(bt.LADDER_W):
            r = refs[w]
            c = (r - L)[sel]
            c = c[np.isfinite(c)]
            if len(c) >= 30:
                wave_std[d, wi] = c.std()
                wave_rng[d, wi] = np.percentile(c, 95) - np.percentile(c, 5)
            e = (x - r)[sel]
            e = e[np.isfinite(e)]
            if len(e) >= 30:
                fast_std[d, wi] = e.std()
            for arr, tgt, sign in ((hi - r, ext, 0), (r - lo, ext, 2), (smax - r, sx, 0), (r - bmin, sx, 2)):
                v = arr[sel]
                v = v[np.isfinite(v)]
                if len(v) >= 30:
                    tgt[d, wi, sign] = np.percentile(v, 99)
                    tgt[d, wi, sign + 1] = v.max()
    out.update({"wave_std_d": wave_std, "wave_rng_d": wave_rng, "fast_std_d": fast_std, "ext_d": ext, "sx_d": sx})
    # хвост амплитуд отклонения от опоры (минутные экстремумы Sell/Buy и минутная середина) — в % и в единицах G, по суткам
    gg = float(np.nanmedian(f["gap_d"]))
    tail = np.zeros((nday, len(TAIL_W), len(TAIL_KINDS), 2, len(TAIL_ABS) + 1), np.int32)
    tail_n = np.zeros((nday, len(TAIL_W), len(TAIL_KINDS)), np.int32)             # все минуты с данными (в tail — только с положительным отклонением)
    for wi, w in enumerate(TAIL_W):
        r = refs[w] if w in refs else bs.rolling_med_centred(x, w)
        for ki, v in enumerate((smax - r, r - bmin, np.abs(x - r))):
            fin = np.isfinite(v)
            for d in range(nday):
                vv = v[(dom == d) & fin]
                tail_n[d, wi, ki] = len(vv)
                vv = vv[vv > 0]
                if len(vv):
                    tail[d, wi, ki, 0] = np.bincount(np.searchsorted(TAIL_ABS, vv, "right"), minlength=len(TAIL_ABS) + 1)
                    if gg > 0:
                        tail[d, wi, ki, 1, :len(TAIL_G) + 1] = np.bincount(np.searchsorted(TAIL_G, vv / gg, "right"), minlength=len(TAIL_G) + 1)[:len(TAIL_G) + 1]
    out["tail"] = tail
    out["tail_n"] = tail_n
    # масштаб пересечения «медленная часть (ref_W − сутки)» = «быстрая часть (середина − ref_W)» по суткам, лог-интерполяция по W
    cross = np.full(nday, np.nan)
    lw = np.log(np.array(bt.LADDER_W, float))
    for d in range(nday):
        diff = wave_std[d] - fast_std[d]                   # убывает по W (медленная падает, быстрая растёт)
        ok = np.isfinite(diff)
        if ok.sum() >= 3:
            dd, ll = diff[ok], lw[ok]
            idx = np.flatnonzero((dd[:-1] > 0) & (dd[1:] <= 0))
            if len(idx):
                i = idx[0]
                cross[d] = float(np.exp(ll[i] + (ll[i + 1] - ll[i]) * dd[i] / (dd[i] - dd[i + 1])))
            elif dd[0] <= 0:
                cross[d] = float(np.exp(ll[0]))            # быстрая больше медленной уже на самом мелком окне — пересечение ниже лестницы
            elif dd[-1] > 0:
                cross[d] = float(np.exp(ll[-1]) * 2)       # медленная больше быстрой на всей лестнице — пересечение выше лестницы
    out["cross_d"] = cross
    # ACF, спектр, линии
    out["acf"] = bt.acf(xt, bt.ACF_LAGS)
    sp = bt.spectrum(xt)
    if sp is not None:
        per, P = sp
        out["band"] = bt.band_shares(per, P)
        out["peak"] = bt.peak_period(per, P, 60, 1000)
        out["peak_all"] = bt.peak_period(per, P, 20, 1000)
    t_ref = utc_ms(2026, 11, 13)
    out["lines"] = {p: bt.line_stats(xt, t0, p, t_ref) for p in LINES}
    out["hour"] = bt.hour_profile(xt, t0, G["days"])
    # активность вокруг фандинга
    a = np.abs(np.diff(np.r_[x, np.nan]))
    med = np.nanmedian(a[a > 0]) if (a > 0).any() else np.nan
    if np.isfinite(med) and med > 0:
        an = a / med
        out["act_fund"] = activity_profile(an, t0, event_times(t0, t1, FUND_H)).astype(np.float32)
        out["act_plac"] = activity_profile(an, t0, event_times(t0, t1, PLACEBO_H)).astype(np.float32)
    return key, out


def main(src, dst, par=3):
    d = pickle.load(open(src, "rb"))
    G.update({"pairs": d["pairs"], "t0": d["t0"], "t1": d["t1"], "days": d["days"]})
    keys = sorted(d["pairs"])
    res = {}
    it = map(pair_slow, keys) if par <= 1 else Pool(par).imap_unordered(pair_slow, keys, chunksize=8)
    for i, (k, o) in enumerate(it):
        res[k] = o
        if i % 200 == 0:
            print(f"{i + 1}/{len(keys)}", flush=True)
    meta = {"t0": d["t0"], "t1": d["t1"], "days": d["days"], "ladder_w": bt.LADDER_W, "acf_lags": bt.ACF_LAGS, "band_edges": bt.BAND_EDGES, "lines": LINES, "off": OFF,
            "fund_times": event_times(d["t0"], d["t1"], FUND_H), "placebo_times": event_times(d["t0"], d["t1"], PLACEBO_H),
            "tail_w": TAIL_W, "tail_abs": TAIL_ABS, "tail_g": TAIL_G, "tail_kinds": TAIL_KINDS}
    pickle.dump({"pairs": res, "meta": meta}, open(dst, "wb"), protocol=4)
    print("done", len(res))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 3)
