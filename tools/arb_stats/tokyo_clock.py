"""Проверка часов бирж на мс-масштабе для сравнения ног в окне: сколько мс разницы между метками E разных бирж по одним и тем же событиям.

Запуск: python3 tokyo_clock.py <каталог книг (с parsed/)> <каталог сделок (с parsed/)> <t0_мс> <t1_мс> <выход.json> [K монет=12] [сегментов=24]
Что считается (всё — на записанных данных, метки E = приём − лаг сборщика, приём — ядро srv, часы srv — chrony, ±0,06 мс к NTP):
  1. лаг «приём − E» по биржам: пол (p0.1 / p1 / p10 / p50) и его почасовой разброс — дрейф часов бирж и маршрута;
  2. опережение/отставание: кросс-корреляция изменений середины книги (бины 1 мс, сумма Δmid в bps, лаги ±300 мс, FFT) по одним монетам на двух биржах,
     в двух шкалах времени — E бирж и приём srv; то же по знаковому потоку сделок (T = E − (E − T) биржи). Положительный пик — вторая биржа пары двигается позже первой.
     Сегменты по 10 мин, равномерно по окну; пик — по сумме монет (каждая нормирована), уточнение — центр масс ±2 мс.
Разделить «часы бирж смещены» и «биржа действительно опережает» по этим данным нельзя; граница — в отчёте.
"""
import glob
import json
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import maker_run  # noqa: E402
from arb_live import symmap_for  # noqa: E402
from common import EXCHANGES, load_pool, norm  # noqa: E402
from gaps_scan import liquid  # noqa: E402

SEG_MS = 600_000
MAXLAG = 300
NFFT = 1 << 20


def cat(work, ex, sym, keys):
    fs = sorted(glob.glob(str(Path(work, ex, sym + ".*.npz"))))
    if not fs:
        return None
    parts = [dict(np.load(f)) for f in fs]
    return {k: np.concatenate([p[k] for p in parts]) for k in keys}


def seg_signal(t, v, s0):
    i = t - s0
    ok = (i >= 0) & (i < SEG_MS)
    return np.bincount(i[ok], weights=v[ok], minlength=SEG_MS)


def peak(curve):
    k = int(np.argmax(curve))
    lo, hi = max(k - 2, 0), min(k + 3, len(curve))
    w = np.clip(curve[lo:hi], 0, None)
    tau = float((np.arange(lo, hi) * w).sum() / w.sum() - MAXLAG) if w.sum() > 0 else float(k - MAXLAG)
    return k - MAXLAG, tau, float(curve[k])


def main(bw, tw, t0, t1, out, K=12, nseg=24):
    bw, tw = str(Path(bw, "parsed")), str(Path(tw, "parsed"))
    day = time.strftime("%Y-%m-%d", time.gmtime(t1 / 1000))
    symmap, day = symmap_for(day)
    coins = {}
    for ex, m in symmap.items():
        for sym, (coin, mult) in m.items():
            cur = coins.setdefault(coin, {}).get(ex)
            if cur is None or norm(ex, sym)[0] == coin:
                coins[coin][ex] = (sym, mult)
    big = ("binance", "bybit", "okx")
    size = {}
    for c, d in coins.items():
        if all(e in d for e in big):
            tot = 0
            for e in big:
                tot += sum(Path(f).stat().st_size for f in glob.glob(str(Path(bw, e, d[e][0] + ".*.npz"))))
            size[c] = tot
    top = sorted(size, key=lambda c: -size[c])[:K]
    print("coins", top, flush=True)
    res = {"coins": top, "t0": t0, "t1": t1}
    # --- данные
    B, TR = {}, {}
    for c in top:
        for ex in EXCHANGES:
            if ex not in coins[c]:
                continue
            sym, mult = coins[c][ex]
            a = cat(bw, ex, sym, ("e", "rx", "lag", "bid", "ask"))
            if a is None:
                continue
            o = np.lexsort((a["rx"], a["e"]))
            a = {k: v[o] for k, v in a.items()}
            mid = (a["bid"] + a["ask"]) / 2
            dm = np.r_[0.0, np.diff(mid) / mid[:-1] * 1e4]
            B[(c, ex)] = {"e": a["e"], "rx": a["rx"], "lag": a["lag"], "dm": dm}
            t = cat(tw, ex, sym, ("e", "et", "rx", "m"))
            if t is not None and len(t["e"]):
                TR[(c, ex)] = {"T": t["e"] - t["et"], "rx": t["rx"], "s": np.where(t["m"] == 1, -1.0, 1.0)}
    # --- 1. лаг по биржам (все монеты top, все записи книг)
    lag = {}
    for ex in EXCHANGES:
        ls = [(B[(c, ex)]["rx"], B[(c, ex)]["lag"]) for c in top if (c, ex) in B]
        if not ls:
            continue
        rx = np.concatenate([x[0] for x in ls])
        lg = np.concatenate([x[1] for x in ls]).astype(float)
        hr = (rx - t0) // 3_600_000
        hrs = np.unique(hr[(hr >= 0)])
        fl = [float(np.percentile(lg[hr == h], 1)) for h in hrs if (hr == h).sum() >= 200]
        lag[ex] = {"n": int(len(lg)), "pct": {str(p): float(np.percentile(lg, p)) for p in (0.1, 1, 10, 50, 90, 99)}, "neg_share": float((lg < 0).mean()),
                   "hourly_p1": {"min": float(np.min(fl)), "p50": float(np.median(fl)), "max": float(np.max(fl)), "std": float(np.std(fl)), "n": len(fl)}}
    res["lag"] = lag
    # --- 2. кросс-корреляции по сегментам
    starts = [int(t0 + (t1 - t0 - SEG_MS) * k / max(nseg - 1, 1)) for k in range(nseg)]
    taus = {}
    for kind, src, tk in (("book", B, "e"), ("trade", TR, "T")):
        for dom in ("E", "rx"):
            tkey = tk if dom == "E" else "rx"
            vkey = "dm" if kind == "book" else "s"
            for sg, s0 in enumerate(starts):
                curves = xcorr_all_common(src, top, tkey, vkey, s0)
                for (a, b), cur in curves.items():
                    k, tau, h = peak(cur)
                    taus.setdefault(f"{kind}|{dom}|{a}|{b}", []).append([sg, k, tau, h])
            print(kind, dom, "done", flush=True)
    summ = {}
    for key, v in taus.items():
        arr = np.array(v, float)
        t = arr[:, 2]
        summ[key] = {"segments": int(len(arr)), "tau_med": float(np.median(t)), "tau_p25": float(np.percentile(t, 25)), "tau_p75": float(np.percentile(t, 75)),
                     "tau_min": float(t.min()), "tau_max": float(t.max()), "peak_int_med": float(np.median(arr[:, 1])), "height_med": float(np.median(arr[:, 3]))}
    res["xcorr"] = summ
    res["xcorr_raw"] = taus
    json.dump(res, open(out, "w"))
    print("saved", out)


def xcorr_all_common(src, top, tkey, vkey, s0):
    """Корреляции по парам бирж только на монетах, которые есть у обеих."""
    sig = {}
    for (c, ex), d in src.items():
        if c in top:
            sig.setdefault(ex, {})[c] = seg_signal(d[tkey].astype(np.int64), d[vkey], s0)
    out = {}
    exs = [e for e in EXCHANGES if e in sig]
    F = {ex: {c: np.fft.rfft(s, NFFT) for c, s in sig[ex].items()} for ex in exs}
    for i, a in enumerate(exs):
        for b in exs[i + 1:]:
            tot, cnt = np.zeros(2 * MAXLAG + 1), 0
            for c in top:
                if c in sig[a] and c in sig[b]:
                    d = float(np.sqrt((sig[a][c] ** 2).sum() * (sig[b][c] ** 2).sum()))
                    if d <= 0:
                        continue
                    cc = np.fft.irfft(np.conj(F[a][c]) * F[b][c], NFFT)
                    tot += np.r_[cc[-MAXLAG:], cc[:MAXLAG + 1]] / d
                    cnt += 1
            if cnt:
                out[(a, b)] = tot / cnt
    return out


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5], int(sys.argv[6]) if len(sys.argv) > 6 else 12,
         int(sys.argv[7]) if len(sys.argv) > 7 else 24)
