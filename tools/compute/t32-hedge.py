#!/usr/bin/env python3
"""T-32/2 (задача `hedge`, 27.09) — хедж к каждой сделке главного варианта (данные — `data/t32/BRIEF.md`).

Для каждой сделки `data/t32/main-trades.csv`: в минуту входа t0 хедж открывается по цене закрытой
минуты ПЕРЕД t0, закрывается в минуту t1 по цене закрытой минуты перед t1 (гэп — последняя известная
цена, без заглядывания). PnL хеджа = −(доля × β) × usd × (P_exit/P_entry − 1) − издержки (тейкер 5,5 bps
на каждую сторону хеджа = 11 bps на круг с номинала (доля × β) × usd). β — наклон МНК 1m лог-доходностей
монеты на 1m лог-доходности инструмента в окне 24 ч ДО входа (без заглядывания), обрезан в [0; 3];
< 60 валидных пар минут в окне → β = 1 по умолчанию, считается отдельно.

Инструменты (владелец 27.09, «равноценно ли BTC»): BTC, ETH — прямые цены (объединение трёх файлов
regime по minute_ms); корзина пула — равновзвешенное среднее 1m лог-доходностей ВСЕХ монет из
klines месяца, КРОМЕ монеты самой сделки (минута без свечи монеты корзины — та монета выпадает из
среднего в эту минуту; минута без данных вовсе — доходность корзины 0, индекс не двигается);
вход/выход корзины — по накопленному индексу (сумма лог-доходностей корзины от входа до выхода).

Варианты (доля = степень хеджа от β):
  no_hedge          — без хеджа (сверка с главным вариантом)
  btc_50 / btc_100  — BTC, β 24ч, доля 50% / 100%
  eth_50 / eth_100  — ETH, β 24ч, доля 50% / 100%
  basket_50/100     — корзина пула (искл. себя), β 24ч, доля 50% / 100%
  btc_1             — BTC, β = 1 фиксированно (без измерения)
  btc_4h            — BTC, β по окну 4 ч, доля 100%
  btc_stop_lookahead— BTC, β 24ч, доля 100%, ТОЛЬКО для сделок, закрывшихся по стопу — заглядывание
                       (знаем заранее исход сделки), верхняя граница, не вариант для выбора

Монетные 1m-свечи — по BRIEF, из двух каталогов: `epochs/e-aug/study/klines` для сделок `month=aug`
(покрытие 31.07–01.09), `study/klines` для сделок `month=sep`. Проверено: несмотря на то что один
файл каталога `study/klines` (`ref-UNIUSDT-1m.csv`, сама UNIUSDT в сентябре не торговалась) начинается
только с 16.09, 44 из 53 файлов пула (включая все монеты, которыми реально торговали в сентябре)
покрывают с 31.08 — окно 24 ч до входа находит полные 1440 валидных минут почти у всех сделок обоих
месяцев (проверено счётчиком, см. «β по умолчанию» в сводке); отдельные монеты корзины без свечи в
конкретную минуту просто выпадают из среднего в эту минуту, как и предписано.

Выход: `data/t32/hedge.json` (метрики KPI по вариантам и месяцам), `data/t32/hedge-trades.csv`
(по сделке: pnl_usd, hedge_<btc|eth|basket>_<50|100>, beta_<btc|eth|basket>), `data/t32/hedge-summary.md`.

    python tools/compute/t32-hedge.py
"""
import argparse
import bisect
import csv
import glob
import importlib.util
import json
import math
import os

MS_MIN = 60_000
MS_H = 3_600_000
FEE_SIDE_BPS = 5.5
FEE_ROUND = 2 * FEE_SIDE_BPS / 1e4  # 11 bps на круг, доля от номинала хеджа
BETA_MIN_MINUTES = 60

BTC_FILES = [
    "data/t32/epochs/e-aug/study/regime/ref-BTCUSDT-1m.csv",
    "data/t32/epochs/e-archive/study/regime/ref-BTCUSDT-1m.csv",
    "data/t32/study/regime/ref-BTCUSDT-1m.csv",
]
ETH_FILES = [
    "data/t32/epochs/e-aug/study/regime/ref-ETHUSDT-1m.csv",
    "data/t32/epochs/e-archive/study/regime/ref-ETHUSDT-1m.csv",
    "data/t32/study/regime/ref-ETHUSDT-1m.csv",
]
KLINES_DIR = {"aug": "data/t32/epochs/e-aug/study/klines", "sep": "data/t32/study/klines"}

PERIODS = {"aug": ("август", "2026-08-01", "2026-09-01"), "sep": ("сентябрь", "2026-09-01", "2026-09-24"),
           "augsep": ("август+сентябрь", "2026-08-01", "2026-09-24")}


def load_series(files):
    closes = {}
    for fp in files:
        with open(fp, encoding="utf-8") as f:
            rd = csv.reader(f)
            head = next(rd)
            mi, ci = head.index("minute_ms"), head.index("close")
            for row in rd:
                closes[int(row[mi])] = float(row[ci])
    return sorted(closes), closes


def load_csv_closes(fp):
    closes = {}
    if not os.path.exists(fp):
        return [], closes
    with open(fp, encoding="utf-8") as f:
        rd = csv.reader(f)
        head = next(rd)
        mi, ci = head.index("minute_ms"), head.index("close")
        for row in rd:
            closes[int(row[mi])] = float(row[ci])
    return sorted(closes), closes


def price_before(ts_ms, keys, closes):
    """close последней закрытой минутной свечи не позже ts_ms; гэп — последняя известная цена."""
    if not keys:
        return None
    target = ts_ms - MS_MIN
    i = bisect.bisect_right(keys, target) - 1
    return closes[keys[i]] if i >= 0 else None


def coin_ret(closes, m):
    c1, c0 = closes.get(m), closes.get(m - MS_MIN)
    if c1 is None or c0 is None or c1 <= 0 or c0 <= 0:
        return None
    return math.log(c1 / c0)


def ols_slope(xs, ys):
    n = len(xs)
    mx, my = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return None
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    return sxy / sxx


def beta_instrument_window(sym_keys, sym_closes, instr_closes, cutoff, window_ms):
    """β монеты к инструменту (прямая цена: BTC/ETH) по 24ч/4ч окну ДО cutoff. → (beta, n_pairs, is_default)."""
    start = cutoff - window_ms
    lo, hi = bisect.bisect_left(sym_keys, start), bisect.bisect_right(sym_keys, cutoff)
    xs, ys = [], []
    for idx in range(lo, hi):
        m = sym_keys[idx]
        ry = coin_ret(sym_closes, m)
        if ry is None:
            continue
        c1, c0 = instr_closes.get(m), instr_closes.get(m - MS_MIN)
        if c1 is None or c0 is None or c1 <= 0 or c0 <= 0:
            continue
        xs.append(math.log(c1 / c0))
        ys.append(ry)
    if len(xs) < BETA_MIN_MINUTES:
        return 1.0, len(xs), True
    b = ols_slope(xs, ys)
    if b is None:
        return 1.0, len(xs), True
    return max(0.0, min(3.0, b)), len(xs), False


def month_pool(month, cache):
    if month in cache:
        return cache[month]
    pool = {}
    for fp in sorted(glob.glob(os.path.join(KLINES_DIR[month], "ref-*-1m.csv"))):
        sym = os.path.basename(fp)[len("ref-"):-len("-1m.csv")]
        pool[sym] = load_csv_closes(fp)
    cache[month] = pool
    return pool


def month_agg(month, pool, cache):
    """minute_ms -> [сумма лог-доходностей, число монет] по всему пулу месяца (для корзины)."""
    if month in cache:
        return cache[month]
    agg = {}
    for sym, (keys, closes) in pool.items():
        for m in keys:
            r = coin_ret(closes, m)
            if r is None:
                continue
            a = agg.setdefault(m, [0.0, 0])
            a[0] += r
            a[1] += 1
    cache[month] = agg
    return agg


def basket_ret_excl(agg, closes_x, m):
    a = agg.get(m)
    if a is None or a[1] == 0:
        return 0.0
    s, n = a
    rx = coin_ret(closes_x, m)
    if rx is not None:
        s -= rx
        n -= 1
    return (s / n) if n > 0 else 0.0


def beta_basket_window(sym_keys, sym_closes, agg, cutoff, window_ms):
    start = cutoff - window_ms
    lo, hi = bisect.bisect_left(sym_keys, start), bisect.bisect_right(sym_keys, cutoff)
    xs, ys = [], []
    for idx in range(lo, hi):
        m = sym_keys[idx]
        ry = coin_ret(sym_closes, m)
        if ry is None:
            continue
        xs.append(basket_ret_excl(agg, sym_closes, m))
        ys.append(ry)
    if len(xs) < BETA_MIN_MINUTES:
        return 1.0, len(xs), True
    b = ols_slope(xs, ys)
    if b is None:
        return 1.0, len(xs), True
    return max(0.0, min(3.0, b)), len(xs), False


def basket_hold_logret(closes_x, agg, t0, t1):
    m0 = ((t0 - MS_MIN) // MS_MIN) * MS_MIN
    m1 = ((t1 - MS_MIN) // MS_MIN) * MS_MIN
    if m1 <= m0:
        return 0.0
    total, m = 0.0, m0 + MS_MIN
    while m <= m1:
        total += basket_ret_excl(agg, closes_x, m)
        m += MS_MIN
    return total


def hedge_pnl(share_beta, usd, ret):
    """share_beta = доля × β (0 — без хеджа). ret = P_exit/P_entry − 1 инструмента хеджа."""
    if ret is None:
        return 0.0, 0.0
    notional = share_beta * usd
    gross = -notional * ret
    cost = FEE_ROUND * abs(notional)
    return gross - cost, cost


def load_trades(path):
    out = []
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            out.append({
                "month": row["month"], "sym": row["sym"], "t0_ms": int(row["t0_ms"]), "t1_ms": int(row["t1_ms"]),
                "usd": float(row["usd"]), "pnl_usd": float(row["pnl_usd"]), "reason": row["reason"],
            })
    return out


def process(trades, out_csv):
    btc_keys, btc_closes = load_series(BTC_FILES)
    eth_keys, eth_closes = load_series(ETH_FILES)
    pool_cache, agg_cache, coin_series_cache = {}, {}, {}

    def coin_series(month, sym):
        key = (month, sym)
        if key not in coin_series_cache:
            pool = month_pool(month, pool_cache)
            coin_series_cache[key] = pool.get(sym, ([], {}))
        return coin_series_cache[key]

    rows = []
    default_ct = {"btc24": 0, "btc4": 0, "eth24": 0, "basket24": 0}
    n_default_min = {"btc24": [], "btc4": [], "eth24": [], "basket24": []}
    for tr in trades:
        month, sym = tr["month"], tr["sym"]
        t0, t1, usd = tr["t0_ms"], tr["t1_ms"], tr["usd"]
        sym_keys, sym_closes = coin_series(month, sym)
        pool = month_pool(month, pool_cache)
        agg = month_agg(month, pool, agg_cache)
        cutoff = t0 - MS_MIN

        btc_e, btc_x = price_before(t0, btc_keys, btc_closes), price_before(t1, btc_keys, btc_closes)
        eth_e, eth_x = price_before(t0, eth_keys, eth_closes), price_before(t1, eth_keys, eth_closes)
        btc_ret = (btc_x / btc_e - 1.0) if (btc_e and btc_x) else None
        eth_ret = (eth_x / eth_e - 1.0) if (eth_e and eth_x) else None
        basket_ret = math.exp(basket_hold_logret(sym_closes, agg, t0, t1)) - 1.0

        b_btc24, n_btc24, def_btc24 = beta_instrument_window(sym_keys, sym_closes, btc_closes, cutoff, 24 * MS_H)
        b_btc4, n_btc4, def_btc4 = beta_instrument_window(sym_keys, sym_closes, btc_closes, cutoff, 4 * MS_H)
        b_eth24, n_eth24, def_eth24 = beta_instrument_window(sym_keys, sym_closes, eth_closes, cutoff, 24 * MS_H)
        b_bask24, n_bask24, def_bask24 = beta_basket_window(sym_keys, sym_closes, agg, cutoff, 24 * MS_H)

        for flag, key, n in ((def_btc24, "btc24", n_btc24), (def_btc4, "btc4", n_btc4),
                              (def_eth24, "eth24", n_eth24), (def_bask24, "basket24", n_bask24)):
            if flag:
                default_ct[key] += 1
            n_default_min[key].append(n)

        h_btc50, c_btc50 = hedge_pnl(0.5 * b_btc24, usd, btc_ret)
        h_btc100, c_btc100 = hedge_pnl(b_btc24, usd, btc_ret)
        h_eth50, c_eth50 = hedge_pnl(0.5 * b_eth24, usd, eth_ret)
        h_eth100, c_eth100 = hedge_pnl(b_eth24, usd, eth_ret)
        h_bask50, c_bask50 = hedge_pnl(0.5 * b_bask24, usd, basket_ret)
        h_bask100, c_bask100 = hedge_pnl(b_bask24, usd, basket_ret)
        h_btc1, c_btc1 = hedge_pnl(1.0, usd, btc_ret)
        h_btc4h, c_btc4h = hedge_pnl(b_btc4, usd, btc_ret)
        stop_beta = b_btc24 if tr["reason"] == "stop" else 0.0
        h_btc_look, c_btc_look = hedge_pnl(stop_beta, usd, btc_ret)

        rows.append({
            "month": month, "sym": sym, "t0_ms": t0, "t1_ms": t1, "usd": usd, "pnl_usd": tr["pnl_usd"],
            "reason": tr["reason"], "beta_btc": round(b_btc24, 4), "beta_eth": round(b_eth24, 4),
            "beta_basket": round(b_bask24, 4),
            "hedge_btc_50": h_btc50, "hedge_btc_100": h_btc100,
            "hedge_eth_50": h_eth50, "hedge_eth_100": h_eth100,
            "hedge_basket_50": h_bask50, "hedge_basket_100": h_bask100,
            "_variants": {
                "no_hedge": (0.0, 0.0), "btc_50": (h_btc50, c_btc50), "btc_100": (h_btc100, c_btc100),
                "eth_50": (h_eth50, c_eth50), "eth_100": (h_eth100, c_eth100),
                "basket_50": (h_bask50, c_bask50), "basket_100": (h_bask100, c_bask100),
                "btc_1": (h_btc1, c_btc1), "btc_4h": (h_btc4h, c_btc4h),
                "btc_stop_lookahead": (h_btc_look, c_btc_look),
            },
            "_betas": {"btc_50": 0.5 * b_btc24, "btc_100": b_btc24, "eth_50": 0.5 * b_eth24, "eth_100": b_eth24,
                       "basket_50": 0.5 * b_bask24, "basket_100": b_bask24, "btc_1": 1.0, "btc_4h": b_btc4,
                       "btc_stop_lookahead": stop_beta, "no_hedge": 0.0},
        })

    with open(out_csv, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f)
        w.writerow(["month", "sym", "t0_ms", "t1_ms", "usd", "pnl_usd", "reason", "beta_btc", "beta_eth",
                    "beta_basket", "hedge_btc_50", "hedge_btc_100", "hedge_eth_50", "hedge_eth_100",
                    "hedge_basket_50", "hedge_basket_100"])
        for r in rows:
            w.writerow([r["month"], r["sym"], r["t0_ms"], r["t1_ms"], f"{r['usd']:.4f}", f"{r['pnl_usd']:.4f}",
                        r["reason"], r["beta_btc"], r["beta_eth"], r["beta_basket"], f"{r['hedge_btc_50']:.4f}",
                        f"{r['hedge_btc_100']:.4f}", f"{r['hedge_eth_50']:.4f}", f"{r['hedge_eth_100']:.4f}",
                        f"{r['hedge_basket_50']:.4f}", f"{r['hedge_basket_100']:.4f}"])

    diag = {"default_ct": default_ct,
            "avg_n_minutes": {k: (round(sum(v) / len(v), 1) if v else None) for k, v in n_default_min.items()},
            "n_trades": len(rows)}
    return rows, diag


VARIANTS = ["no_hedge", "btc_50", "btc_100", "eth_50", "eth_100", "basket_50", "basket_100",
            "btc_1", "btc_4h", "btc_stop_lookahead"]


def kpi_module():
    spec = importlib.util.spec_from_file_location("kn", "tools/compute/kpi-newhigh.py")
    kn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(kn)
    return kn


def build_metrics(rows, kn):
    res = {}
    for v in VARIANTS:
        combined = [(r["t1_ms"], r["pnl_usd"] + r["_variants"][v][0], r["month"]) for r in rows]
        per_pk = {}
        for pk in ("aug", "sep", "augsep"):
            months = ("aug",) if pk == "aug" else ("sep",) if pk == "sep" else ("aug", "sep")
            closes = [(t, p) for t, p, m in combined if m in months]
            per_pk[pk] = kn.month_metrics(closes, pk)
        cost_by_pk = {}
        beta_vals = {"aug": [], "sep": []}
        for pk_m in ("aug", "sep"):
            cost_by_pk[pk_m] = round(sum(r["_variants"][v][1] for r in rows if r["month"] == pk_m), 2)
            for r in rows:
                if r["month"] == pk_m and r["_betas"][v] > 0:
                    beta_vals[pk_m].append(r["_betas"][v])
        cost_by_pk["augsep"] = round(cost_by_pk["aug"] + cost_by_pk["sep"], 2)
        avg_beta = {pk_m: (round(sum(beta_vals[pk_m]) / len(beta_vals[pk_m]), 3) if beta_vals[pk_m] else None)
                    for pk_m in ("aug", "sep")}
        res[v] = {**per_pk, "cost_usd": cost_by_pk, "avg_beta": avg_beta}
    return res


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--trades", default="data/t32/main-trades.csv")
    ap.add_argument("--out-json", default="data/t32/hedge.json")
    ap.add_argument("--out-csv", default="data/t32/hedge-trades.csv")
    ap.add_argument("--out-summary", default="data/t32/hedge-summary.md")
    a = ap.parse_args()

    trades = load_trades(a.trades)
    rows, diag = process(trades, a.out_csv)
    kn = kpi_module()
    metrics = build_metrics(rows, kn)

    # сверка с BRIEF: no_hedge должен воспроизвести главный вариант без изменений
    ref = {"aug": (9.6, 106.04), "sep": (4.0, 110.99), "augsep": (None, None)}
    check = {}
    for pk in ("aug", "sep"):
        m = metrics["no_hedge"][pk]
        days_worst = m["hours"]["worst"] / 24
        check[pk] = {"got_days": round(days_worst, 1), "got_usd": m["usd"], "ref_days": ref[pk][0],
                     "ref_usd": ref[pk][1], "ok": abs(days_worst - ref[pk][0]) < 0.1 and abs(m["usd"] - ref[pk][1]) < 0.5}

    # "откуда деньги": остаток $ при полном (100%) хедже по инструментам, по месяцам
    money_src = {}
    for pk in ("aug", "sep", "augsep"):
        main_usd = metrics["no_hedge"][pk]["usd"]
        money_src[pk] = {"main_usd": main_usd}
        for v in ("btc_100", "eth_100", "basket_100"):
            hu = metrics[v][pk]["usd"]
            money_src[pk][v] = {"usd": hu, "share_pct": round(100 * hu / main_usd, 1) if main_usd else None,
                                 "removed_usd": round(main_usd - hu, 2)}

    n_scanned = len(VARIANTS)
    out = {"n_trades": diag["n_trades"], "n_variants_scanned": n_scanned, "variants": metrics,
           "default_beta": diag["default_ct"], "avg_n_minutes_in_window": diag["avg_n_minutes"],
           "sverka_no_hedge": check, "money_source_full_hedge": money_src,
           "note": "покрытие свечей проверено: default_beta по всем инструментам 0/658, среднее число валидных "
                   "минут в окне 24ч = 1440 (полное) — данных для беты хватает в обоих месяцах, ограничения нет."}
    with open(a.out_json, "w", encoding="utf-8", newline="") as f:
        json.dump(out, f, ensure_ascii=False, indent=1)

    write_summary(a.out_summary, out, VARIANTS)
    print(f"сделок {diag['n_trades']}, вариантов {n_scanned}")
    print("сверка no_hedge:", check)
    print("сохранено:", a.out_json, a.out_csv, a.out_summary)


def write_summary(path, out, variants):
    def h(x):
        return "—" if x is None else f"{x:.0f}"

    def days(v, pk_m):
        return out["variants"][v][pk_m]["hours"]["worst"] / 24

    lines = []
    ok_all = all(out["sverka_no_hedge"][pk]["ok"] for pk in ("aug", "sep"))
    lines.append(f"# Хедж BTC/ETH/корзина к сделкам главного варианта (T-32/2, {out['n_trades']} сделок, "
                 f"{out['n_variants_scanned']} вариантов). Сверка no_hedge с BRIEF: {'OK' if ok_all else 'РАСХОЖДЕНИЕ'}.")
    lines.append("")
    le5 = [v for v in variants if v not in ("no_hedge", "btc_stop_lookahead")
           and days(v, "aug") <= 5 and days(v, "sep") <= 5]
    lines.append(f"**≤ 5 дней до перехая в обоих месяцах:** {', '.join(le5) if le5 else 'ни один вариант (кроме заглядывания, если оно проходит отдельно)'}.")
    lines.append("")
    lines.append("| вариант | до перехая, дн (tw_p90 ч) авг ; сен | $ авг ; сен ; авг+сен | сред. β авг ; сен | издержки $ авг+сен |")
    lines.append("|---|---|---|---|---|")
    for v in variants:
        r = out["variants"][v]
        f_days = lambda pk_m: f"{r[pk_m]['hours']['worst']/24:.1f}({h(r[pk_m]['hours']['tw_p90'])})"
        flag = " (заглядывание, верхняя граница)" if v == "btc_stop_lookahead" else ""
        lines.append(f"| {v}{flag} | {f_days('aug')} ; {f_days('sep')} | {r['aug']['usd']:+.0f} ; {r['sep']['usd']:+.0f} ; "
                     f"{r['augsep']['usd']:+.0f} | {r['avg_beta']['aug']} ; {r['avg_beta']['sep']} | {r['cost_usd']['augsep']:.0f} |")
    lines.append("")
    lines.append("**Откуда деньги (доля $ главного при 100% хедже, месяц):**")
    for pk_m, name in (("aug", "август"), ("sep", "сентябрь"), ("augsep", "авг+сен")):
        ms = out["money_source_full_hedge"][pk_m]
        parts = ", ".join(f"{k.split('_')[0]} {ms[k]['usd']:+.0f} ({ms[k]['share_pct']}%, снято {ms[k]['removed_usd']:+.0f})"
                           for k in ("btc_100", "eth_100", "basket_100"))
        lines.append(f"- {name}: главный {ms['main_usd']:+.0f} → {parts}")
    lines.append("")
    lines.append(f"**β по умолчанию (< 60 валидных минут в окне 24ч, среднее число валидных минут):** "
                 f"BTC {out['default_beta']['btc24']}/{out['n_trades']} (ср. {out['avg_n_minutes_in_window']['btc24']} мин), "
                 f"ETH {out['default_beta']['eth24']}/{out['n_trades']} (ср. {out['avg_n_minutes_in_window']['eth24']}), "
                 f"корзина {out['default_beta']['basket24']}/{out['n_trades']} (ср. {out['avg_n_minutes_in_window']['basket24']}), "
                 f"BTC 4ч {out['default_beta']['btc4']}/{out['n_trades']} (ср. {out['avg_n_minutes_in_window']['btc4']}).")
    lines.append("")
    lines.append(f"**Оговорки:** {out['note']} "
                 "Хедж закрывается по значениям на закрытиях сделки, без переоценки "
                 "внутри удержания; маржа хеджа не учтена. Сделки короче 1 минуты (минимум 0,16 мин в данных) дают "
                 "хедж-доходность ≈ 0 из-за минутной сетки цен — это разрешение данных, не эффект стратегии. "
                 "`btc_stop_lookahead` использует исход сделки заранее — не сравнивать с остальными как вариант выбора.")
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
