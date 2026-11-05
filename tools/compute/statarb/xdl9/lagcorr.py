#!/usr/bin/env python3
"""TK-493 G1b(б): кросс-корреляция минутных доходностей BTC, ETH, SOL между биржами на лагах −3…+3 минуты.

stdlib + numpy. Читает свечи только трёх символов с каждой биржи (каталог --core: <биржа>/candles/<SYMBOL>.csv.gz, Hyperliquid —
candles15/), строит сетку минут окна, доходность r[t] = ln(c[t] / c[t−1]) ТОЛЬКО там, где есть обе соседние свечи (пропуски не
заполняются), и считает Пирсона corr(rA[t], rB[t+k]) по минутам, где обе доходности определены. Лаг k > 0: ряд B повторяет ряд A
через k минут (отметка B «позже»). Пик обязан быть на k = 0; пара с пиком не в 0 помечается, а биржа, участвующая во всех
помеченных парах, названа в сводке.
Дополнительно по BTC: уровень относительно эталона (первая биржа списка): медиана и 1/99-й процентили ln(cA/cB) в bps, стандартное
отклонение минутной доходности и автокорреляция лага 1 (эффект отскока цены сделок; у сглаженных рядов — mark/index/оракул — другая).
Hyperliquid отдаёт только 15-минутки: его сверяют с остальными, пересобранными в 15-минутные бары (close = close последней минуты бара),
на лагах −2…+2 баров по 15 минут.

usage: lagcorr.py --core DIR --out DIR [--start 2026-10-01] [--end 2026-11-05] [--ref binance]
"""
import argparse, csv, datetime, gzip, itertools, json, os, sys
import numpy as np

NAMES = {
    "BTC": {"binance": "BTCUSDT", "bybit": "BTCUSDT", "okx": "BTC-USDT-SWAP", "bingx": "BTC-USDT", "aster": "BTCUSDT", "kraken": "PF_XBTUSD",
            "lighter": "BTC", "bitget": "BTCUSDT", "hyperliquid": "BTC"},
    "ETH": {"binance": "ETHUSDT", "bybit": "ETHUSDT", "okx": "ETH-USDT-SWAP", "bingx": "ETH-USDT", "aster": "ETHUSDT", "kraken": "PF_ETHUSD",
            "lighter": "ETH", "bitget": "ETHUSDT", "hyperliquid": "ETH"},
    "SOL": {"binance": "SOLUSDT", "bybit": "SOLUSDT", "okx": "SOL-USDT-SWAP", "bingx": "SOL-USDT", "aster": "SOLUSDT", "kraken": "PF_SOLUSD",
            "lighter": "SOL", "bitget": "SOLUSDT", "hyperliquid": "SOL"},
}
MIN_EXCH = ["binance", "bybit", "okx", "bingx", "aster", "kraken", "lighter", "bitget"]
LAGS = list(range(-3, 4))
LAGS15 = list(range(-2, 3))


def parse_day(s):
    return int(datetime.datetime.strptime(s[:10], "%Y-%m-%d").replace(tzinfo=datetime.timezone.utc).timestamp() * 1000)


def load_close(path, start, end, step):
    n = (end - start) // step
    c = np.full(n, np.nan)
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        f.readline()
        for line in f:
            p = line.split(",")
            t = int(p[0])
            if start <= t < end and (t - start) % step == 0:
                c[(t - start) // step] = float(p[4])
    return c


def rets(c):
    r = np.full(len(c), np.nan)
    with np.errstate(invalid="ignore", divide="ignore"):
        r[1:] = np.log(c[1:] / c[:-1])
    return r


def corr_lag(ra, rb, k):
    """corr(ra[t], rb[t+k]) по t, где обе определены. Возвращает (corr, n)."""
    n = len(ra)
    if k >= 0:
        a, b = ra[:n - k], rb[k:]
    else:
        a, b = ra[-k:], rb[:n + k]
    m = np.isfinite(a) & np.isfinite(b)
    if m.sum() < 30:
        return float("nan"), int(m.sum())
    return float(np.corrcoef(a[m], b[m])[0, 1]), int(m.sum())


def to15(c, start, step=60_000):
    """Минутные close -> 15-минутные: close последней минуты бара (NaN, если её нет)."""
    n = len(c) // 15
    return c[14:14 + 15 * n:15].copy()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--core", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--start", default="2026-10-01")
    ap.add_argument("--end", default="2026-11-05")
    ap.add_argument("--ref", default="binance")
    ap.add_argument("--exchanges", default=",".join(MIN_EXCH))
    a = ap.parse_args()
    start, end = parse_day(a.start), parse_day(a.end)
    os.makedirs(a.out, exist_ok=True)
    exs = [e for e in a.exchanges.split(",") if os.path.isdir(os.path.join(a.core, e, "candles"))]
    print("биржи с минутными данными:", exs)
    closes = {}
    for sym in NAMES:
        for ex in exs:
            p = os.path.join(a.core, ex, "candles", NAMES[sym][ex] + ".csv.gz")
            closes[(sym, ex)] = load_close(p, start, end, 60_000)
    rows = []
    anomalies = {}
    for sym in NAMES:
        R = {ex: rets(closes[(sym, ex)]) for ex in exs}
        for A, B in itertools.combinations(exs, 2):
            cs, ns = [], []
            for k in LAGS:
                c, n = corr_lag(R[A], R[B], k)
                cs.append(c)
                ns.append(n)
            peak = LAGS[int(np.nanargmax(cs))] if not all(np.isnan(cs)) else None
            rows.append([sym, A, B] + ["%.4f" % x for x in cs] + [peak, ns[LAGS.index(0)]])
            if peak != 0:
                anomalies.setdefault((sym, A, B), (peak, cs))
    with open(os.path.join(a.out, "lagcorr.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["symbol", "A", "B"] + ["k=%+d" % k for k in LAGS] + ["peak_lag", "n_pairs_k0"])
        w.writerows(rows)
    # сводка: пары с пиком не в 0, число таких пар по биржам
    per_ex = {e: 0 for e in exs}
    for (sym, A, B), (pk, cs) in anomalies.items():
        per_ex[A] += 1
        per_ex[B] += 1
    total_pairs = len(rows)
    print("пар (символ × биржа × биржа): %d, с пиком не на лаге 0: %d" % (total_pairs, len(anomalies)))
    for (sym, A, B), (pk, cs) in sorted(anomalies.items()):
        print("  НЕ 0: %s %s–%s пик %+d | %s" % (sym, A, B, pk, " ".join("%+d:%.3f" % (k, c) for k, c in zip(LAGS, cs))))
    print("число помеченных пар по биржам (из %d пар на биржу ×3 символа):" % (len(exs) - 1), {e: v for e, v in per_ex.items()})
    # уровень, волатильность и автокорреляция BTC
    ref = a.ref if a.ref in exs else exs[0]
    lv = []
    for ex in exs:
        c = closes[("BTC", ex)]
        r = rets(c)
        m = np.isfinite(r)
        std = float(np.std(r[m]) * 1e4)
        x, y = r[1:], r[:-1]
        mm = np.isfinite(x) & np.isfinite(y)
        ac1 = float(np.corrcoef(x[mm], y[mm])[0, 1]) if mm.sum() > 30 else float("nan")
        d = np.log(c / closes[("BTC", ref)]) * 1e4
        d = d[np.isfinite(d)]
        lv.append(dict(exchange=ex, btc_minutes=int(np.isfinite(c).sum()), ret_std_bps=round(std, 3), ret_ac1=round(ac1, 4),
                       level_median_bps=round(float(np.median(d)), 3), level_p1_bps=round(float(np.percentile(d, 1)), 3),
                       level_p99_bps=round(float(np.percentile(d, 99)), 3)))
    with open(os.path.join(a.out, "btc_level.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(lv[0].keys()))
        w.writeheader()
        w.writerows(lv)
    print("BTC: уровень относительно %s, std минутной доходности, автокорреляция лага 1" % ref)
    for r in lv:
        print("  ", r)
    # Hyperliquid 15 мин
    hl_dir = os.path.join(a.core, "hyperliquid", "candles15")
    rows15 = []
    if os.path.isdir(hl_dir):
        for sym in NAMES:
            hl = load_close(os.path.join(hl_dir, NAMES[sym]["hyperliquid"] + ".csv.gz"), start, end, 900_000)
            rh = rets(hl)
            for ex in exs:
                rx = rets(to15(closes[(sym, ex)], start))
                cs = []
                ns = 0
                for k in LAGS15:
                    c, n = corr_lag(rx, rh, k)   # k > 0: Hyperliquid позже биржи ex на k баров
                    cs.append(c)
                    if k == 0:
                        ns = n
                pk = LAGS15[int(np.nanargmax(cs))] if not all(np.isnan(cs)) else None
                rows15.append([sym, ex, "hyperliquid"] + ["%.4f" % x for x in cs] + [pk, ns])
        with open(os.path.join(a.out, "lagcorr_hl15.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.writer(f)
            w.writerow(["symbol", "A", "B"] + ["k=%+d" % k for k in LAGS15] + ["peak_lag", "n_pairs_k0"])
            w.writerows(rows15)
        bad = [r for r in rows15 if r[-2] != 0]
        print("Hyperliquid 15 м: пар %d, пик не в 0: %d" % (len(rows15), len(bad)))
        for r in bad:
            print("  НЕ 0:", r)
    # компактные таблицы для отчёта: корреляция на лагах для BTC (все пары) и сводка пиков
    json.dump(dict(total_pairs=total_pairs, anomalies=[dict(symbol=s, A=A, B=B, peak=pk) for (s, A, B), (pk, cs) in anomalies.items()],
                   per_exchange=per_ex), open(os.path.join(a.out, "lagcorr_summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)


if __name__ == "__main__":
    main()
