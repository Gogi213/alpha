#!/usr/bin/env python3
"""Широкий поиск связи признаков на входе с результатом сделки — по месяцам (владелец 25.09: «посмотреть ещё что-то
на корреляцию, для вариативности»). Сделки — `loss-days.py --csv` (как в счёте дашборда); признаки считаются здесь из
минутных свечей BTC (`study/regime/ref-BTCUSDT-1m.csv`), монет (`study/klines/ref-<SYM>-1m.csv`), файлов режима и
самого списка сделок. Все признаки — по последней **закрытой** минуте до входа (известны в момент входа).

Для каждого признака и месяца: ранговая корреляция (Спирмен) с $ сделки и со стопом, $ и стопы по третям (границы —
по обоим месяцам вместе). «Одинаково в обоих» — знак корреляции с $ совпал и |ρ| ≥ `--min-rho` в каждом месяце.
Чтение на тех же данных, не вердикт: при ~25 признаках часть совпадёт случайно — кандидат идёт в предрегистрацию.

    python3 loss-corr.py --trades study/loss-days-btc4h.csv \\
        --homes август=epochs/e-aug --homes сентябрь=epochs/e-archive,. \\
        --klines август=epochs/e-aug/study/klines --klines сентябрь=study/klines \\
        --csv study/loss-corr-btc4h.csv > study/loss-corr-btc4h.txt
"""
import argparse
import bisect
import csv
import datetime as dt
import os
from collections import defaultdict

MIN_MS = 60_000


class Bars:
    """Минутные свечи одного инструмента из нескольких каталогов: минута → (high, low, close, volume)."""

    def __init__(self, paths):
        self.d = {}
        for p in paths:
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    for r in csv.DictReader(f):
                        self.d[int(r["minute_ms"])] = (float(r["high"]), float(r["low"]), float(r["close"]), float(r.get("volume") or 0))
        self.k = sorted(self.d)

    def close(self, m):
        i = bisect.bisect_right(self.k, m) - 1
        return self.d[self.k[i]][2] if i >= 0 and m - self.k[i] <= 5 * MIN_MS else None

    def window(self, m0, minutes):
        lo, hi = bisect.bisect_left(self.k, m0 - (minutes - 1) * MIN_MS), bisect.bisect_right(self.k, m0)
        return [self.d[x] for x in self.k[lo:hi]]


def ret(bars, m, minutes):
    a, b = bars.close(m - minutes * MIN_MS), bars.close(m)
    return (b / a - 1) * 1e4 if a and b else None


def rng(bars, m, minutes):
    w = bars.window(m, minutes)
    c = bars.close(m)
    return (max(x[0] for x in w) - min(x[1] for x in w)) / c * 1e4 if w and c else None


def off_low(bars, m, minutes):
    w = bars.window(m, minutes)
    c = bars.close(m)
    return (c / min(x[1] for x in w) - 1) * 1e4 if w and c else None


def rv(bars, m, minutes):
    """Realized volatility: корень суммы квадратов минутных лог-доходностей за окно, bps."""
    import math
    w = bars.window(m, minutes + 1)
    cl = [x[2] for x in w]
    if len(cl) < minutes // 2:
        return None
    return math.sqrt(sum(math.log(b / a) ** 2 for a, b in zip(cl, cl[1:]) if a > 0 and b > 0)) * 1e4


def vol_ratio(bars, m):
    h, d = bars.window(m, 60), bars.window(m, 1440)
    vd = sum(x[3] for x in d)
    return sum(x[3] for x in h) / (vd / 24) if h and vd else None


def rv_feats(c, btc, m):
    c1, c4, c24, b1, b24 = rv(c, m, 60), rv(c, m, 240), rv(c, m, 1440), rv(btc, m, 60), rv(btc, m, 1440)
    return {"coin_rv_1h": c1, "coin_rv_4h": c4, "coin_rv_24h": c24,
            "coin_rv_ratio": c1 / c24 * 24 ** 0.5 if c1 and c24 else None,
            "btc_rv_1h": b1, "btc_rv_24h": b24, "btc_rv_ratio": b1 / b24 * 24 ** 0.5 if b1 and b24 else None,
            "coin_rv_to_btc": c24 / b24 if c24 and b24 else None}


def ranks(xs):
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        for k in range(i, j + 1):
            r[order[k]] = (i + j) / 2
        i = j + 1
    return r


def spearman(x, y):
    pairs = [(a, b) for a, b in zip(x, y) if a is not None and b is not None]
    if len(pairs) < 20:
        return None
    rx, ry = ranks([p[0] for p in pairs]), ranks([p[1] for p in pairs])
    n = len(pairs)
    mx, my = sum(rx) / n, sum(ry) / n
    cov = sum((a - mx) * (b - my) for a, b in zip(rx, ry))
    sx = sum((a - mx) ** 2 for a in rx) ** 0.5
    sy = sum((b - my) ** 2 for b in ry) ** 0.5
    return cov / (sx * sy) if sx and sy else None


FEATURES = [
    ("btc_5m", "BTC за 5 мин до входа, bps"), ("btc_15m", "BTC за 15 мин"), ("btc_30m", "BTC за 30 мин"),
    ("btc_1h", "BTC за 1 ч"), ("btc_4h", "BTC за 4 ч"), ("btc_24h", "BTC за сутки"),
    ("btc_range_1h", "размах BTC за 1 ч, bps"), ("btc_off_low_4h", "BTC над минимумом 4 ч, bps"),
    ("eth_minus_btc_1h", "ETH минус BTC за 1 ч"), ("pool_1h", "пул за 1 ч"), ("pool_4h", "пул за 4 ч"),
    ("coin_15m", "монета за 15 мин"), ("coin_1h", "монета за 1 ч"), ("coin_4h", "монета за 4 ч"), ("coin_24h", "монета за сутки"),
    ("coin_minus_btc_1h", "монета минус BTC за 1 ч"), ("coin_minus_btc_4h", "монета минус BTC за 4 ч"),
    ("coin_range_1h", "размах монеты за 1 ч, bps"), ("coin_off_low_4h", "монета над минимумом 4 ч, bps"),
    ("coin_vol_ratio", "объём монеты за час к среднему часу суток"),
    ("coin_rv_1h", "RV монеты за 1 ч, bps"), ("coin_rv_4h", "RV монеты за 4 ч, bps"), ("coin_rv_24h", "RV монеты за сутки, bps"),
    ("coin_rv_ratio", "RV монеты: час к суткам (×√24)"), ("btc_rv_1h", "RV BTC за 1 ч, bps"), ("btc_rv_24h", "RV BTC за сутки, bps"),
    ("btc_rv_ratio", "RV BTC: час к суткам (×√24)"), ("coin_rv_to_btc", "RV монеты к RV BTC за сутки"),
    ("open_at_entry", "открытых позиций в момент входа"), ("rank_in_wave", "номер входа в волне"),
    ("mins_since_wave", "минут от начала волны"), ("prev_same_loss", "прошлая сделка по монете в минус (1/0)"),
    ("prev_same_mins", "минут с прошлой сделки по монете"), ("weekend", "выходной (1/0)"), ("hour_local", "час GMT+4"),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--trades", required=True)
    ap.add_argument("--homes", action="append", required=True, help="месяц=дом[,дом] — свечи BTC и файлы режима")
    ap.add_argument("--klines", action="append", required=True, help="месяц=каталог[,каталог] — свечи монет")
    ap.add_argument("--min-rho", type=float, default=0.05)
    ap.add_argument("--csv")
    a = ap.parse_args()
    homes = {s.split("=", 1)[0]: s.split("=", 1)[1].split(",") for s in a.homes}
    kl = {s.split("=", 1)[0]: s.split("=", 1)[1].split(",") for s in a.klines}
    trades = defaultdict(list)
    with open(a.trades, encoding="utf-8") as f:
        for r in csv.DictReader(f):
            trades[r["month"]].append(r)

    out_rows, stats = [], {}
    for month, rows in trades.items():
        btc = Bars([os.path.join(h, "study", "regime", "ref-BTCUSDT-1m.csv") for h in homes[month]])
        eth = Bars([os.path.join(h, "study", "regime", "ref-ETHUSDT-1m.csv") for h in homes[month]])
        pool = {}
        for h in homes[month]:
            d = os.path.join(h, "study", "regime")
            for name in sorted(os.listdir(d)) if os.path.isdir(d) else []:
                if name[:2] == "20" and name.endswith(".csv"):
                    with open(os.path.join(d, name), encoding="utf-8") as fh:
                        for r in csv.DictReader(fh):
                            if r.get("pool_ret_1h_bps"):
                                pool[int(r["minute_ms"])] = (float(r["pool_ret_1h_bps"]), float(r["pool_ret_4h_bps"] or "nan"))
        coins = {}
        rows.sort(key=lambda r: int(r["t0_ns"]))
        wave_start, wave_rank, last_by_coin = {}, {}, {}
        for i, r in enumerate(rows):
            t0, t1 = int(r["t0_ns"]) // 1_000_000, int(r["t1_ns"]) // 1_000_000
            m = t0 // MIN_MS * MIN_MS - MIN_MS
            sym = r["symbol"]
            if sym not in coins:
                coins[sym] = Bars([os.path.join(d, f"ref-{sym}-1m.csv") for d in kl[month]])
            c = coins[sym]
            e = r["episode"]
            wave_start.setdefault(e, t0)
            wave_rank[e] = wave_rank.get(e, 0) + 1
            prev = last_by_coin.get(sym)
            b1, b4 = ret(btc, m, 60), ret(btc, m, 240)
            c1, c4 = ret(c, m, 60), ret(c, m, 240)
            e1 = ret(eth, m, 60)
            p = pool.get(m)
            lt = dt.datetime.fromtimestamp(t0 / 1000, dt.timezone(dt.timedelta(hours=4)))
            feat = {
                "btc_5m": ret(btc, m, 5), "btc_15m": ret(btc, m, 15), "btc_30m": ret(btc, m, 30), "btc_1h": b1, "btc_4h": b4,
                "btc_24h": ret(btc, m, 1440), "btc_range_1h": rng(btc, m, 60), "btc_off_low_4h": off_low(btc, m, 240),
                "eth_minus_btc_1h": None if e1 is None or b1 is None else e1 - b1,
                "pool_1h": p[0] if p else None, "pool_4h": p[1] if p and p[1] == p[1] else None,
                "coin_15m": ret(c, m, 15), "coin_1h": c1, "coin_4h": c4, "coin_24h": ret(c, m, 1440),
                "coin_minus_btc_1h": None if c1 is None or b1 is None else c1 - b1,
                "coin_minus_btc_4h": None if c4 is None or b4 is None else c4 - b4,
                "coin_range_1h": rng(c, m, 60), "coin_off_low_4h": off_low(c, m, 240), "coin_vol_ratio": vol_ratio(c, m),
                **rv_feats(c, btc, m),
                "open_at_entry": sum(1 for q in rows[:i] if int(q["t1_ns"]) // 1_000_000 > t0),
                "rank_in_wave": wave_rank[e], "mins_since_wave": (t0 - wave_start[e]) / MIN_MS,
                "prev_same_loss": None if prev is None else (1.0 if prev[1] <= 0 else 0.0),
                "prev_same_mins": None if prev is None else (t0 - prev[0]) / MIN_MS,
                "weekend": 1.0 if lt.weekday() >= 5 else 0.0, "hour_local": float(lt.hour),
            }
            last_by_coin[sym] = (t1, float(r["pnl_usd"]))
            out_rows.append({"month": month, "symbol": sym, "t0_ns": r["t0_ns"], "reason": r["reason"],
                             "pnl_usd": r["pnl_usd"], **{k: (None if v is None else round(v, 3)) for k, v in feat.items()}})
        stats[month] = [x for x in out_rows if x["month"] == month]

    months = list(stats)
    print("признак | " + " | ".join(f"{m}: ρ с $ / ρ со стопом / $ по третям (низ·сер·верх)" for m in months) + " | одинаково в обоих")
    summary = []
    for key, label in FEATURES:
        vals = sorted(x[key] for m in months for x in stats[m] if x[key] is not None)
        if len(vals) < 30:
            continue
        lo_b, hi_b = vals[len(vals) // 3], vals[2 * len(vals) // 3]
        cells, rhos = [], []
        for m in months:
            xs = [x[key] for x in stats[m]]
            pn = [float(x["pnl_usd"]) for x in stats[m]]
            stp = [1.0 if x["reason"] == "stop" else 0.0 for x in stats[m]]
            rp, rs = spearman(xs, pn), spearman(xs, stp)
            t = [0.0, 0.0, 0.0]
            for x, p in zip(xs, pn):
                if x is not None:
                    t[0 if x <= lo_b else (1 if x <= hi_b else 2)] += p
            rhos.append(rp)
            cells.append(f"{'—' if rp is None else f'{rp:+.2f}'} / {'—' if rs is None else f'{rs:+.2f}'} / "
                         f"{t[0]:+.0f}·{t[1]:+.0f}·{t[2]:+.0f}")
        same = all(r is not None and abs(r) >= a.min_rho for r in rhos) and len({r > 0 for r in rhos}) == 1
        summary.append((same, min(abs(r) for r in rhos if r is not None) if all(r is not None for r in rhos) else 0, label, cells, lo_b, hi_b))
    for same, _, label, cells, lo_b, hi_b in sorted(summary, key=lambda s: (not s[0], -s[1])):
        print(f"{label} (трети ≤{lo_b:.0f} / ≤{hi_b:.0f}) | " + " | ".join(cells) + f" | {'ДА' if same else 'нет'}")
    if a.csv and out_rows:
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(out_rows[0]))
            w.writeheader()
            w.writerows(out_rows)


if __name__ == "__main__":
    main()
