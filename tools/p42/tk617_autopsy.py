#!/usr/bin/env python3
"""TK-617: разбор баз П-42 по слоям (описательно, 01.07–13.11) → markdown. Вход — `trades_feat.parquet` (`tk617_build.py`).

Единица статистики — сутки: интервал среднего на сделку — круговой блочный бутстреп по суткам (блок 4), как §13.24; медиан по P&L нет.
Корзины непрерывных признаков — квартили (или заданные границы) по объединённой выборке L_base + S_base, одни и те же для обеих сторон.
"""
import argparse
import os
import sys

import numpy as np
import pandas as pd

NDAYS = 136
BLOCK = 4
NB = 2000
MONTHS = ["jul", "aug", "sep", "oct", "nov"]
NOTIONAL = 500.0
rng = np.random.default_rng(617)


def agg(df):
    n = len(df)
    s = float(df["gf"].sum())
    return n, s, (s / n if n else float("nan"))


def ci_mean(df, nb=NB):
    """95 % интервал среднего на сделку: круговой блочный бутстреп по суткам (ΣS/ΣN)."""
    if len(df) < 8:
        return float("nan"), float("nan")
    sd = np.bincount(df["abs_day"].values.astype(int), weights=df["gf"].values, minlength=NDAYS)[:NDAYS]
    nd = np.bincount(df["abs_day"].values.astype(int), minlength=NDAYS)[:NDAYS].astype(float)
    nbk = -(-NDAYS // BLOCK)
    st = rng.integers(0, NDAYS, size=(nb, nbk))
    ix = ((st[:, :, None] + np.arange(BLOCK)[None, None, :]) % NDAYS).reshape(nb, -1)[:, :NDAYS]
    S, N = sd[ix].sum(1), nd[ix].sum(1)
    ok = N > 0
    m = S[ok] / N[ok]
    return float(np.percentile(m, 2.5)), float(np.percentile(m, 97.5))


def cell(df, ci=True):
    n, s, m = agg(df)
    if n == 0:
        return "0 / — / —"
    if ci:
        lo, hi = ci_mean(df)
        return "%d / %+.1f / %+.2f [%+.2f; %+.2f]" % (n, s, m, lo, hi)
    return "%d / %+.1f / %+.2f" % (n, s, m)


def md(rows, head):
    out = ["| " + " | ".join(head) + " |", "|" + "---|" * len(head)]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def bins_of(x, pooled, edges=None, q=4, fmt="%.2f", scale=1.0):
    """Корзины: квартили pooled (или заданные границы). Возвращает (метка по строкам, список меток по порядку)."""
    v = pooled.dropna().values
    if edges is None:
        edges = list(np.unique(np.quantile(v, np.linspace(0, 1, q + 1)[1:-1])))
    cuts = [-np.inf] + list(edges) + [np.inf]
    labs = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        if a == -np.inf:
            labs.append("≤ " + fmt % (b * scale))
        elif b == np.inf:
            labs.append("> " + fmt % (a * scale))
        else:
            labs.append("(" + fmt % (a * scale) + "; " + fmt % (b * scale) + "]")
    lab = pd.cut(x, cuts, labels=labs).astype(object)
    lab[x.isna().values] = "нет данных"
    return lab, labs + ["нет данных"]


def strata_table(T, var, title, edges=None, q=4, fmt="%.2f", scale=1.0, sets=("L_base", "S_base"), label_col=None, order=None, ci=True):
    pooled = T[T["set"].isin(sets)][var]
    if label_col is None:
        lab, order = bins_of(T[var], pooled, edges, q, fmt, scale)
        T = T.assign(_b=lab)
    else:
        T = T.assign(_b=T[label_col])
        order = order or sorted(T["_b"].dropna().unique())
    rows = []
    for b in order:
        r = [b]
        for st in sets:
            r.append(cell(T[(T["set"] == st) & (T["_b"] == b)], ci))
        if all(c.startswith("0 /") for c in r[1:]):
            continue
        rows.append(r)
    return "#### %s\n\n(n / Σ $ / $ на сделку [95 %% блочный бутстреп по суткам])\n\n" % title + md(rows, ["корзина"] + list(sets))


def month_strata(T, var, title, sets=("L_base", "S_base"), **kw):
    """Среднее на сделку по корзинам и месяцам для L_base (устойчивость по месяцам)."""
    pooled = T[T["set"].isin(sets)][var]
    lab, order = bins_of(T[var], pooled, kw.get("edges"), kw.get("q", 4), kw.get("fmt", "%.2f"), kw.get("scale", 1.0))
    T = T.assign(_b=lab)
    out = []
    for st in sets:
        rows = []
        for b in order:
            r = [b]
            for m in MONTHS:
                d = T[(T["set"] == st) & (T["_b"] == b) & (T["month"] == m)]
                n, s, mu = agg(d)
                r.append("%d / %+.1f" % (n, s) if n else "—")
            rows.append(r)
        out.append("**%s**\n\n" % st + md(rows, ["корзина"] + MONTHS))
    return "#### %s: помесячно (n / Σ $)\n\n" % title + "\n\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--feat", default="/data/p42-tk617/trades_feat.parquet")
    ap.add_argument("--out", default="/data/p42-tk617/autopsy.md")
    a = ap.parse_args()
    T = pd.read_parquet(a.feat)
    T["win_"] = T["gf"] > 0
    # L_mk2 TK-616 (только чтение готовых сделок): ключ (окно, монета, уровень, sig_ms) — заполнился ли у мейкер-ретеста тот же сигнал
    keys = set()
    for w in ("wa", "wb", "wc"):
        pth = os.path.join(os.path.dirname(a.feat), "mk2", "L_mk2-%s.csv" % w)
        if os.path.exists(pth):
            m = pd.read_csv(pth, usecols=["sym", "level", "sig_ms"])
            keys |= {(w, r.sym, r.level, r.sig_ms) for r in m.itertuples()}
    T["mk2_fill"] = [1 if (r.win, r.sym, r.level, r.sig_ms) in keys else 0 for r in T.itertuples()] if keys else np.nan
    o = []
    P = o.append

    # ---- 0. сверка баз -------------------------------------------------------------------------------------------------------
    P("## 0. Сверка рядов с отчётом TK-599 («Шаг 0»)\n")
    rows = []
    for st in ("L_base", "S_base", "L_sig", "S_sig"):
        d = T[T["set"] == st]
        rows.append([st, len(d), "%+.2f" % d["gf"].sum(), "%+.3f" % d["gf"].mean()] + ["%+.2f / %d" % (d[d["month"] == m]["gf"].sum(), (d["month"] == m).sum()) for m in MONTHS])
    P(md(rows, ["ряд", "сделок", "Σ $", "$/сделку"] + MONTHS))

    # ---- 1. месяцы и рынок ---------------------------------------------------------------------------------------------------
    P("\n## 1. Месяцы и режим рынка\n")
    rows = []
    for m in MONTHS:
        d = T[(T["set"] == "L_base") & (T["month"] == m)]
        rows.append([m, "%+.1f%%" % (d["btc_r7d"].mean() * 100), "%+.1f%%" % (d["mkt_r7d"].mean() * 100), "%.1f" % d["btc_vol24h"].mean(),
                     cell(T[(T["set"] == "L_base") & (T["month"] == m)], False), cell(T[(T["set"] == "S_base") & (T["month"] == m)], False)])
    P("Средние по сделкам L_base месяца (описательно): доходность BTC / среднего рынка за предыдущие 7 суток на входе, реализованная волатильность BTC (бп/мин, 24 ч).\n")
    P(md(rows, ["месяц", "BTC 7д до входа", "рынок 7д до входа", "vol BTC 24ч, бп/мин", "L_base n / Σ / $ на сделку", "S_base n / Σ / $ на сделку"]))

    # ---- 2. выходы -----------------------------------------------------------------------------------------------------------
    P("\n## 2. Причина выхода (kind)\n")
    rows = []
    for st in ("L_base", "S_base"):
        for k in ("trail", "rt", "be", "tx", "stop", "forced"):
            d = T[(T["set"] == st) & (T["kind"] == k)]
            if not len(d):
                continue
            rows.append([st, k, len(d), "%+.1f" % d["gf"].sum(), "%+.2f" % d["gf"].mean(), "%.1f" % (d["hold_s"].mean() / 60), "%d" % d["takes"].mean()
                         if False else "%.1f" % d["takes"].mean()] + ["%+.1f / %d" % (d[d["month"] == m]["gf"].sum(), (d["month"] == m).sum()) for m in MONTHS])
    P(md(rows, ["ряд", "выход", "n", "Σ $", "$/сделку", "удержание, мин (среднее)", "тейков (среднее)"] + MONTHS))

    # ---- 3. MFE / MAE --------------------------------------------------------------------------------------------------------
    P("\n## 3. MFE / MAE (по ленте; NATR сигнала; удержание = от входа до выхода)\n")
    rows = []
    for st in ("L_base", "S_base"):
        for k in ("trail", "rt", "be", "tx", "stop"):
            d = T[(T["set"] == st) & (T["kind"] == k)]
            if len(d) < 5:
                continue
            rows.append([st, k, len(d), "%.2f" % d["mfe_n"].mean(), "%.2f / %.2f" % tuple(np.quantile(d["mfe_n"].dropna(), [.75, .9])), "%.2f" % d["mae_n"].mean(),
                         "%.2f" % d["mfe60_n"].mean(), "%.2f" % d["mae60_n"].mean(), "%.0f%%" % (100 * d["touch_h"].mean()), "%.1f%%" % (100 * (d["t_touch"] >= 0).mean())])
    P(md(rows, ["ряд", "выход", "n", "MFE удерж., NATR (среднее)", "MFE p75 / p90", "MAE удерж., NATR", "MFE 60 мин", "MAE 60 мин", "коснулись уровня за удержание", "коснулись уровня за 60 мин"]))
    P("\nУбыточные сделки по follow-through (MFE за удержание, NATR сигнала): нет продолжения (< 0,5) / частично (0,5–2) / отдали (≥ 2; трейл включается при +2 NATR):\n")
    rows = []
    for st in ("L_base", "S_base"):
        d = T[(T["set"] == st) & (T["gf"] < 0)]
        for nm, m in (("MFE < 0,5", d["mfe_n"] < 0.5), ("0,5 ≤ MFE < 2", (d["mfe_n"] >= 0.5) & (d["mfe_n"] < 2)), ("MFE ≥ 2", d["mfe_n"] >= 2)):
            x = d[m]
            rows.append([st, nm, len(x), "%+.1f" % x["gf"].sum(), "%+.2f" % (x["gf"].mean() if len(x) else np.nan)])
    P(md(rows, ["ряд", "убыточные: класс", "n", "Σ $", "$ на сделку"]))
    P("\nЗахват трейла: Σ$ выигрышей kind=trail против Σ MFE (бп × 500 $ / 1e4):\n")
    rows = []
    for st in ("L_base", "S_base"):
        d = T[(T["set"] == st) & (T["kind"] == "trail")]
        rows.append([st, len(d), "%+.1f" % d["gf"].sum(), "%.1f" % (d["mfe_h"].sum() * NOTIONAL / 1e4), "%.0f%%" % (100 * d["gf"].sum() / (d["mfe_h"].sum() * NOTIONAL / 1e4))])
    P(md(rows, ["ряд", "n trail", "Σ реализовано $", "Σ MFE $-эквивалент", "захват"]))

    # ---- 4. слои -------------------------------------------------------------------------------------------------------------
    P("\n## 4. Слои (база: L_base и S_base рядом; корзины — квартили объединённой выборки)\n")
    spec = [
        ("btc_r1h", "BTC: доходность за 1 ч до сигнала, %", dict(scale=100, fmt="%.2f")),
        ("btc_r4h", "BTC: за 4 ч до сигнала, %", dict(scale=100, fmt="%.2f")),
        ("btc_r24h", "BTC: за 24 ч до сигнала, %", dict(scale=100, fmt="%.2f")),
        ("btc_r7d", "BTC: за 7 сут. до сигнала, %", dict(scale=100, fmt="%.1f")),
        ("mkt_r4h", "Средний рынок (566 монет): за 4 ч до сигнала, %", dict(scale=100, fmt="%.2f")),
        ("mkt_r24h", "Средний рынок: за 24 ч, %", dict(scale=100, fmt="%.2f")),
        ("brd_r4h", "Ширина рынка (доля монет в плюсе за 4 ч)", dict(fmt="%.2f")),
        ("brd_r24h", "Ширина рынка (доля монет в плюсе за 24 ч)", dict(fmt="%.2f")),
        ("coin_r1h", "Монета: доходность за 1 ч до сигнала, %", dict(scale=100, fmt="%.2f")),
        ("coin_r24h", "Монета: за 24 ч до сигнала, %", dict(scale=100, fmt="%.1f")),
        ("rel_r4h", "Монета минус BTC за 4 ч, %", dict(scale=100, fmt="%.2f")),
        ("btc_vol4h", "Волатильность BTC (σ минутных доходностей за 4 ч, бп)", dict(fmt="%.1f")),
        ("btc_vol24h", "Волатильность BTC (σ минутных за 24 ч, бп)", dict(fmt="%.1f")),
        ("mkt_vol4h", "Волатильность среднего рынка (σ минутных за 4 ч, бп)", dict(fmt="%.1f")),
        ("btc_day", "ОПИСАТЕЛЬНО, день входа целиком (не причинно): доходность BTC за сутки UTC, %", dict(scale=100, fmt="%.2f")),
        ("mkt_day", "ОПИСАТЕЛЬНО, день входа целиком (не причинно): доходность среднего рынка за сутки, %", dict(scale=100, fmt="%.2f")),
        ("turn24", "Оборот монеты за 24 ч до сигнала, млн $", dict(scale=1e-6, fmt="%.0f")),
        ("turn_rank", "Ранг монеты по обороту за 24 ч (1 — самая ликвидная из 566)", dict(fmt="%.0f")),
        ("natr_bp", "NATR (1м/30) на сигнале, бп", dict(fmt="%.0f")),
        ("dist_bp", "Расстояние до уровня на сигнале, бп", dict(fmt="%.1f")),
        ("n_members", "Число пивотов уровня", dict(q=3, fmt="%.0f")),
        ("lvl_age_min", "Возраст уровня на сигнале, ч", dict(scale=1 / 60, fmt="%.0f")),
        ("fund_rate", "Ставка фандинга (последняя до входа), бп", dict(scale=1e4, fmt="%.2f")),
        ("hold_s", "ОПИСАТЕЛЬНО (после факта): удержание, мин", dict(scale=1 / 60, fmt="%.1f")),
    ]
    for var, title, kw in spec:
        if var not in T:
            continue
        P(strata_table(T, var, title, **kw))
        P("")
    P(strata_table(T, "hour_utc", "Время суток, UTC (GMT+4 = +4 ч)", label_col="hour_b", order=["00–06", "06–12", "12–18", "18–24"]) if False else "")
    T["hour_b"] = pd.cut(T["hour_utc"], [-1, 5, 11, 17, 23], labels=["00–06", "06–12", "12–18", "18–24"]).astype(str)
    P(strata_table(T, "hour_utc", "Время входа, UTC (владельцу GMT+4: +4 ч)", label_col="hour_b", order=["00–06", "06–12", "12–18", "18–24"]))
    T["dow_b"] = np.where(T["dow"] >= 5, "сб–вс", "пн–пт")
    P("\n" + strata_table(T, "dow", "День недели", label_col="dow_b", order=["пн–пт", "сб–вс"]))
    T["fund_sign"] = np.where(T["fund_rate"].isna(), "нет данных", np.where(T["fund_rate"] > 0, "> 0", np.where(T["fund_rate"] < 0, "< 0", "= 0")))
    P("\n" + strata_table(T, "fund_rate", "Знак фандинга (последняя ставка до входа; > 0 — лонги платят)", label_col="fund_sign", order=["< 0", "= 0", "> 0", "нет данных"]))
    P("\n" + strata_table(T, "touch_h", "ОПИСАТЕЛЬНО (после факта): коснулась ли цена уровня за удержание", label_col="touch_h", order=[0, 1]))

    # ---- 5. помесячная устойчивость ключевых слоёв ---------------------------------------------------------------------------
    P("\n## 5. Устойчивость по месяцам (ключевые слои)\n")
    for var, title, kw in (("btc_r24h", "BTC 24 ч", dict(scale=100, fmt="%.2f", q=3)), ("turn_rank", "ранг оборота", dict(fmt="%.0f", q=3)), ("natr_bp", "NATR", dict(fmt="%.0f", q=3))):
        P(month_strata(T, var, title, **kw))
        P("")

    # ---- 6. маркаут сырого сигнала -------------------------------------------------------------------------------------------
    P("\n## 6. Маркаут сырого сигнала (сигнальный режим: все первые сигналы уровней; бп в пользу стороны от цены входа; без выходов, без комиссий)\n")
    P("Издержки круга для справки: комиссия тейкера 2 × 3,15 бп + проскальзывание 2 × 1 бп ≈ 8,3 бп (мейкер-ноги дешевле).\n")
    rows = []
    for st in ("L_sig", "S_sig"):
        for m in MONTHS + ["all"]:
            d = T[(T["set"] == st) & ((T["month"] == m) if m != "all" else True)]
            rows.append([st, m, len(d)] + ["%+.1f" % d["mo_%d" % h].mean() for h in (1, 5, 15, 60)] + ["%.0f%%" % (100 * (d["mo_60"] > 0).mean())])
    P(md(rows, ["ряд", "месяц", "n", "через 1 мин", "5 мин", "15 мин", "60 мин", "доля > 0 через 60 мин"]))
    P("\nМаркаут по слоям (сигнальный режим; среднее, бп):\n")
    for var, title, kw in (("btc_r24h", "BTC за 24 ч", dict(scale=100, fmt="%.2f")), ("natr_bp", "NATR", dict(fmt="%.0f")), ("turn_rank", "ранг оборота", dict(fmt="%.0f")), ("mkt_r4h", "рынок за 4 ч", dict(scale=100, fmt="%.2f"))):
        pooled = T[T["set"].isin(["L_sig", "S_sig"])][var]
        lab, order = bins_of(T[var], pooled, kw.get("edges"), 4, kw.get("fmt", "%.2f"), kw.get("scale", 1.0))
        Tt = T.assign(_b=lab)
        rows = []
        for b in order:
            r = [b]
            for st in ("L_sig", "S_sig"):
                d = Tt[(Tt["set"] == st) & (Tt["_b"] == b)]
                r.append("%d: " % len(d) + " / ".join("%+.1f" % d["mo_%d" % h].mean() for h in (1, 5, 15, 60)) if len(d) else "—")
            if r[1] != "—" or r[2] != "—":
                rows.append(r)
        P("**%s** (n: 1 / 5 / 15 / 60 мин)\n\n" % title + md(rows, ["корзина", "L_sig", "S_sig"]) + "\n")

    # ---- 6b. механика уровня: касание, пробой, возврат ------------------------------------------------------------------------
    P("\n## 6b. Механика уровня на сигнальных рядах (L_sig / S_sig): касается ли цена уровня, идёт ли за него, возвращается ли\n")
    P("Касание = сделка по ту сторону уровня (лонг ≥ уровня, шорт ≤ уровня) в течение 60 мин от входа; пробой k NATR = за 60 мин цена ушла за уровень не менее чем на k NATR сигнала; "
      "возврат = после первого касания цена опускалась (лонг; шорт зеркально) на ≥ 0,2 % под уровень.\n")
    rows = []
    for st in ("L_sig", "S_sig"):
        for m in MONTHS + ["all"]:
            d = T[(T["set"] == st) & ((T["month"] == m) if m != "all" else True)]
            tt = d[d["t_touch"] >= 0]
            k = lambda x: "%.0f%%" % (100 * x)       # noqa: E731
            rows.append([st, m, len(d), k((d["t_touch"] >= 0).mean()), k(((d["t_touch"] >= 0) & (d["t_touch"] <= 900_000)).mean()),
                         k((tt["exc_lvl_60"] / tt["natr_bp"] >= 1).mean()) if len(tt) else "—", k((tt["exc_lvl_60"] / tt["natr_bp"] >= 3).mean()) if len(tt) else "—",
                         k((tt["dep_after_touch_60"] <= -20).mean()) if len(tt) else "—", "%+.1f" % tt["mo_60"].mean() if len(tt) else "—",
                         "%+.1f" % d[d["t_touch"] < 0]["mo_60"].mean() if (d["t_touch"] < 0).any() else "—"])
    P(md(rows, ["ряд", "месяц", "n", "касание ≤ 60 мин", "касание ≤ 15 мин", "пробой ≥ 1 NATR (из коснувшихся)", "пробой ≥ 3 NATR", "возврат ≥ 0,2 % (из коснувшихся)",
                "маркаут 60 мин: коснулись, бп", "маркаут 60 мин: не коснулись, бп"]))

    # ---- 7. ретест (диагностическая подсказка по TK-616 L_mk2) ---------------------------------------------------------------
    P("\n## 7. Диагностика: что отделяет условие ретеста (прокси mk2 по ленте)\n")
    P("Прокси: после сигнала + 4 мс в той же минуте была сделка за уровнем (пробой) и после неё сделка строго с этой стороны уровня (ретест). Тик цены не учтён; сверка — с TK-616 «оба» (237 сделок, Σ тейкера +367,74) / «только тейкер» (384, −360,68) на L_base.\n")
    rows = []
    for st in ("L_base", "S_base"):
        d = T[T["set"] == st]
        for nm, m in (("пробой и ретест в минуте сигнала", d["retest_min"] == 1), ("пробой без ретеста", (d["pierce_min"] == 1) & (d["retest_min"] == 0)), ("нет пробоя в минуте сигнала", d["pierce_min"] == 0)):
            x = d[m]
            rows.append([st, nm, cell(x), ", ".join("%s %d" % (k, v) for k, v in x["kind"].value_counts().items())])
    P(md(rows, ["ряд", "группа", "n / Σ / $ на сделку [CI]", "выходы"]))
    P("")
    if T["mk2_fill"].notna().any():
        P("\nГруппы по L_base: «заполнился у L_mk2» — тот же сигнал (окно, монета, уровень, `sig_ms`) есть в сделках L_mk2 TK-616 (`out-pre-5b2d4e6a`, только чтение); сверка с отчётом TK-616: «оба» 237 сделок, Σ тейкера +367,74; «только тейкер» 384, −360,68.\n")
        d = T[T["set"] == "L_base"]
        rows = []
        for nm, m in (("заполнился у mk2", d["mk2_fill"] == 1), ("не заполнился", d["mk2_fill"] == 0)):
            x = d[m]
            rows.append([nm, cell(x), ", ".join("%s %d" % (k, v) for k, v in x["kind"].value_counts().items()), "%.0f%%" % (100 * x["touch_h"].mean()),
                         "%.1f" % (x["hold_s"].mean() / 60), ", ".join("%s %+.0f" % (mm, x[x["month"] == mm]["gf"].sum()) for mm in MONTHS)])
        P(md(rows, ["группа L_base", "n / Σ / $ на сделку [CI]", "выходы (тейкер-вход)", "касание уровня за удержание", "удержание, мин", "Σ по месяцам"]))
        rows = []
        for var in ("natr_bp", "dist_bp", "turn_rank", "n_members", "lvl_age_min", "btc_r24h", "mkt_r4h", "btc_vol4h", "rel_r4h", "coin_r1h", "mfe_n", "mae_n", "mfe60_n", "mo_1", "mo_5", "mo_15", "mo_60"):
            rows.append([var, "%.4g" % d[d["mk2_fill"] == 1][var].mean(), "%.4g" % d[d["mk2_fill"] == 0][var].mean()])
        P("\nСредние признаков по группам (L_base):\n\n" + md(rows, ["признак", "заполнился", "не заполнился"]))

    # ---- 8. аудит симметрии ---------------------------------------------------------------------------------------------------
    P("\n## 8. Аудит симметрии лонг / шорт (знаки, цены, комиссия, фандинг)\n")
    A = T[T["set"].isin(["L_base", "S_base"])].copy()
    A["e_px"] = A["entry_px_e8"] / 1e8
    A["res_in_bp"] = A["s"] * (A["e_px"] / A["px_in_tape"] - 1) * 1e4          # ожидание: +1 бп проскальзывания в сторону хуже
    one = A[(A["takes"] == 0) & A["px_out_tape"].notna()].copy()
    one["x_impl"] = one["e_px"] * (1 + one["s"] * one["gross"] / NOTIONAL)           # цена выхода, подразумеваемая gross (позиция $500 одним лотом)
    one["res_out_bp"] = one["s"] * (one["x_impl"] / one["px_out_tape"] - 1) * 1e4     # ожидание: −1 бп проскальзывания (хуже для сделки)
    rows = []
    for st in ("L_base", "S_base"):
        for k in ("rt", "stop", "be", "tx"):
            d = one[(one["set"] == st) & (one["kind"] == k)]
            if len(d) < 3:
                continue
            rows.append([st, k, len(d), "%+.2f" % d["res_out_bp"].mean(), "%.2f" % d["res_out_bp"].abs().max(), "%+.2f" % A[(A["set"] == st) & (A["kind"] == k)]["res_in_bp"].mean(),
                         "%.3f" % d["fees"].mean(), "%+.3f" % d["gross"].mean()])
    P("Одноногие выходы (takes = 0): цена выхода, подразумеваемая gross, против цены ленты на момент выхода (бп, в пользу стороны; ожидание — около −1 бп проскальзывания), вход против первой сделки ленты (ожидание около +1 бп хуже), комиссия.\n")
    P(md(rows, ["ряд", "выход", "n", "остаток выхода, бп (среднее)", "|макс|", "остаток входа, бп (среднее)", "комиссия $ (среднее)", "gross $ (среднее)"]))
    rows = []
    for st in ("L_base", "S_base"):
        d = A[(A["set"] == st) & (A["month"].isin(["sep", "oct", "nov"]))]
        rows.append([st, len(d), "%+.2f" % d["funding"].sum(), "%+.4f" % d["funding"].mean(), "%.1f%%" % (100 * (d["funding"] != 0).mean()), "%+.2f" % (d["fund_rate"].mean() * 1e4)])
    P("\nФандинг движка (сентябрь–ноябрь; июль–август фандинга в движке нет):\n")
    P(md(rows, ["ряд", "сделок", "Σ фандинг $", "$ на сделку", "доля сделок с фандингом", "средняя ставка до входа, бп"]))

    open(a.out, "w", encoding="utf-8").write("\n".join(o) + "\n")
    print("written", a.out, len(o))


if __name__ == "__main__":
    main()
