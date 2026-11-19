#!/usr/bin/env python3
"""TK-617 v2: хвост жёстких стопов лонга П-42 (описательно, 01.07–13.11) → markdown. Вход — `trades_feat.parquet` + минутные матрицы (`tk617_build.py`).

Вопросы: что за сделки с kind=stop (монета, возраст листинга, памп до сигнала, час, BTC, скорость −5 %, ликвидность), предсказуемы ли на входе
(AUC причинных признаков, блочный бутстреп по суткам, Холм по числу признаков), не слишком ли тесен уровень стопа 5 % (MAE выигрышей против стопа),
что отделяет ретест-подмножество L_mk2 (TK-616). Подбора нет: только замеры и слои.
"""
import argparse
import json
import os

import numpy as np
import pandas as pd

from tk617_autopsy import agg, bins_of, cell, md

G0_MS = 1781654400000          # 17.06.2026 00:00 UTC
NMIN = 150 * 1440
NDAYS = 136
BLOCK = 4
NB = 2000
MONTHS = ["jul", "aug", "sep", "oct", "nov"]
rng = np.random.default_rng(6172)


def add_extra(T, C, Q, syms):
    """Признаки, которых нет в trades_feat: возраст листинга, памп/просадка монеты за 24 ч, ход BTC и монеты за сделку (описательно)."""
    ix = {s: i for i, s in enumerate(syms)}
    btc = ix["BTCUSDT"]
    ci = np.array([ix[s] for s in T["sym"]])
    m_sig = ((T["t_sig"].values - G0_MS) // 60_000).astype(np.int64)
    m_in = ((T["t_in"].values - G0_MS) // 60_000).astype(np.int64)
    m_out = np.minimum(((T["t_out"].values - G0_MS) // 60_000).astype(np.int64), NMIN - 1)
    first = {}
    n = len(T)
    new_list, age_d, up24, dd24, btc_tr, coin_tr, rng1 = (np.full(n, np.nan) for _ in range(7))
    for k in range(n):
        c = ci[k]
        if c not in first:
            v = np.asarray(C[c], dtype=float)
            nz = np.flatnonzero(~np.isnan(v))
            first[c] = int(nz[0]) if len(nz) else -1
        f = first[c]
        m = int(m_sig[k])
        new_list[k] = 1.0 if f > 0 else 0.0
        age_d[k] = (m - f) / 1440.0 if f >= 0 else np.nan        # для f = 0 — нижняя граница (с начала сетки 17.06)
        if m - 1441 >= 0:
            w = np.asarray(C[c, m - 1441:m], dtype=float)
            if not np.all(np.isnan(w)):
                last = w[~np.isnan(w)][-1]
                up24[k] = last / np.nanmin(w) - 1.0
                dd24[k] = last / np.nanmax(w) - 1.0
        a, b = int(m_in[k]) - 1, int(m_out[k])
        if a >= 0:
            bc0, bc1 = float(C[btc, a]), float(C[btc, b])
            cc0, cc1 = float(C[c, a]), float(C[c, b])
            btc_tr[k] = bc1 / bc0 - 1.0
            coin_tr[k] = cc1 / cc0 - 1.0
    T["new_list"], T["age_d"], T["coin_up24"], T["coin_dd24"], T["btc_trade"], T["coin_trade"] = new_list, age_d, up24, dd24, btc_tr, coin_tr
    T["rel_trade"] = T["coin_trade"] - T["btc_trade"]
    return T


def auc(x, y):
    """P(x_событие > x_остальные) + ½ P(=): AUC через ранги; x, y — numpy, y булев."""
    ok = ~np.isnan(x)
    x, y = x[ok], y[ok]
    n1, n0 = int(y.sum()), int((~y).sum())
    if n1 == 0 or n0 == 0:
        return np.nan
    r = pd.Series(x).rank(method="average").values
    return (r[y].sum() - n1 * (n1 + 1) / 2) / (n1 * n0)


def boot_auc(df, var, ev, nb=NB):
    """AUC признака `var` для события `ev` (булев столбец) + 95 % интервал и двусторонний p круговым блочным бутстрепом по суткам."""
    x, y, d = df[var].values.astype(float), df[ev].values.astype(bool), df["abs_day"].values.astype(int)
    a0 = auc(x, y)
    if np.isnan(a0) or y.sum() < 5:
        return a0, np.nan, np.nan, np.nan
    rows = [np.flatnonzero(d == k) for k in range(NDAYS)]
    nbk = -(-NDAYS // BLOCK)
    st = rng.integers(0, NDAYS, size=(nb, nbk))
    vals = []
    for i in range(nb):
        ds = ((st[i][:, None] + np.arange(BLOCK)[None, :]) % NDAYS).ravel()[:NDAYS]
        r = np.concatenate([rows[k] for k in ds])
        a = auc(x[r], y[r])
        if not np.isnan(a):
            vals.append(a)
    v = np.array(vals)
    p = 2 * min((v <= 0.5).mean(), (v >= 0.5).mean())
    return a0, float(np.percentile(v, 2.5)), float(np.percentile(v, 97.5)), max(p, 1.0 / len(v))


def holm(ps):
    """Скорректированные по Холму p (список; nan остаются nan)."""
    idx = [i for i, p in enumerate(ps) if not np.isnan(p)]
    o = sorted(idx, key=lambda i: ps[i])
    m = len(o)
    out = [np.nan] * len(ps)
    run = 0.0
    for r, i in enumerate(o):
        run = max(run, min(1.0, (m - r) * ps[i]))
        out[i] = run
    return out


FEATS = [  # причинные признаки (известны до входа)
    ("btc_r1h", "BTC 1 ч, %", 100), ("btc_r4h", "BTC 4 ч, %", 100), ("btc_r24h", "BTC 24 ч, %", 100), ("btc_r7d", "BTC 7 сут, %", 100),
    ("mkt_r4h", "рынок 4 ч, %", 100), ("mkt_r24h", "рынок 24 ч, %", 100), ("brd_r4h", "ширина 4 ч", 1), ("brd_r24h", "ширина 24 ч", 1),
    ("coin_r1h", "монета 1 ч, %", 100), ("coin_r4h", "монета 4 ч, %", 100), ("coin_r24h", "монета 24 ч, %", 100), ("coin_up24", "подъём от 24 ч-минимума, %", 100),
    ("coin_dd24", "просадка от 24 ч-максимума, %", 100), ("rel_r4h", "монета − BTC 4 ч, %", 100), ("btc_vol4h", "σ BTC 4 ч, бп", 1), ("btc_vol24h", "σ BTC 24 ч, бп", 1),
    ("mkt_vol4h", "σ рынка 4 ч, бп", 1), ("turn24", "оборот 24 ч, млн $", 1e-6), ("turn1h", "оборот 1 ч, млн $", 1e-6), ("turn_rank", "ранг оборота (1 = ликвидная)", 1),
    ("natr_bp", "NATR, бп", 1), ("dist_bp", "расстояние до уровня, бп", 1), ("n_members", "пивотов уровня", 1), ("lvl_age_min", "возраст уровня, ч", 1 / 60),
    ("age_d", "возраст монеты в сетке, сут", 1), ("new_list", "листинг внутри окна (0/1)", 1), ("fund_rate", "фандинг, бп", 1e4), ("hour_utc", "час UTC", 1), ("dow", "день недели", 1),
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default="/data/p42-tk617")
    ap.add_argument("--out", default="/data/p42-tk617/stops.md")
    a = ap.parse_args()
    T = pd.read_parquet(os.path.join(a.dir, "trades_feat.parquet"))
    C = np.load(os.path.join(a.dir, "mat_close.npy"), mmap_mode="r")
    Q = np.load(os.path.join(a.dir, "mat_qv.npy"), mmap_mode="r")
    syms = json.load(open(os.path.join(a.dir, "mat_syms.json")))
    T = add_extra(T, C, Q, syms)
    T["is_stop"] = T["kind"] == "stop"
    keys = set()
    for w in ("wa", "wb", "wc"):
        pth = os.path.join(a.dir, "mk2", "L_mk2-%s.csv" % w)
        if os.path.exists(pth):
            m = pd.read_csv(pth, usecols=["sym", "level", "sig_ms"])
            keys |= {(w, r.sym, r.level, r.sig_ms) for r in m.itertuples()}
    T["mk2_fill"] = [1 if (r.win, r.sym, r.level, r.sig_ms) in keys else 0 for r in T.itertuples()]
    T["ev5"] = T["mae_60"] <= -500            # сырое событие: за 60 мин от входа цена ушла против на ≥ 5 % (без выходов движка)
    T["ev3"] = T["mae_60"] <= -300
    L = T[T["set"] == "L_base"].copy()
    S = T[T["set"] == "S_base"].copy()
    o = []
    P = o.append

    # ---- A. масштаб ----------------------------------------------------------------------------------------------------------
    P("## A. Масштаб хвоста стопов\n")
    rows = []
    for st, d in (("L_base", L), ("S_base", S)):
        for k in ("stop", "trail", "rt", "be"):
            x = d[d["kind"] == k]
            rows.append([st, k, len(x), "%+.2f" % x["gf"].sum(), "%+.2f" % x["gf"].mean() if len(x) else "—", "%.1f%%" % (100 * len(x) / len(d))] +
                        ["%d / %+.1f" % ((x["month"] == m).sum(), x[x["month"] == m]["gf"].sum()) for m in MONTHS])
    P(md(rows, ["ряд", "выход", "n", "Σ $", "$/сделку", "доля сделок"] + MONTHS))
    st_l = L[L["is_stop"]].sort_values("t_in")
    P("\nL_base kind=stop: %d сделок, Σ %+.2f $; takes: %s; дней со стопами: %d; монет: %d; стопов в днях с ≥ 2: %d.\n" % (
        len(st_l), st_l["gf"].sum(), dict(st_l["takes"].value_counts()), st_l["abs_day"].nunique(), st_l["sym"].nunique(),
        int(st_l["abs_day"].map(st_l["abs_day"].value_counts()).ge(2).sum())))

    # ---- B. сами сделки ------------------------------------------------------------------------------------------------------
    P("\n## B. Все стопы L_base (сорт. по времени входа; UTC; признаки — на сигнале)\n")
    rows = []
    for r in st_l.itertuples():
        dt = pd.to_datetime(r.t_in, unit="ms", utc=True)
        rows.append([dt.strftime("%m-%d %H:%M"), r.sym, "%.0f" % r.natr_bp, "%.0f" % r.dist_bp, "%d" % r.turn_rank,
                     ("%.0f" % r.age_d) if r.new_list == 1 else "≥%.0f" % r.age_d, "%+.1f" % (r.coin_r1h * 100), "%+.1f" % (r.coin_r24h * 100),
                     "%+.1f" % (r.coin_up24 * 100), "%+.2f" % (r.btc_r4h * 100), "%+.2f" % (r.btc_trade * 100), "%+.1f" % (r.coin_trade * 100), "%.1f" % (r.hold_s / 60),
                     "%.2f" % r.mfe_n, "%d" % r.touch_h, "%+.1f" % r.gf])
    P(md(rows, ["вход", "монета", "NATR бп", "до уровня бп", "ранг оборота", "возраст сут", "монета 1ч %", "24ч %", "подъём от мин24 %", "BTC 4ч %", "BTC за сделку %",
                "монета за сделку %", "удержание мин", "MFE NATR", "касание", "$"]))

    # ---- C. сравнение стопов и остальных -------------------------------------------------------------------------------------
    P("\n## C. Стопы против остальных L_base (средние; описательно)\n")
    rows = []
    for var, nm, sc in FEATS + [("btc_trade", "ОПИСАТЕЛЬНО: ход BTC за сделку, %", 100), ("coin_trade", "ОПИСАТЕЛЬНО: ход монеты за сделку, %", 100),
                                ("rel_trade", "ОПИСАТЕЛЬНО: монета − BTC за сделку, %", 100)]:
        a1, a0 = L[L["is_stop"]][var].dropna(), L[~L["is_stop"]][var].dropna()
        rows.append([nm, len(a1), "%.3g" % (a1.mean() * sc), "%.3g" % (a0.mean() * sc), "%.3g" % (a1.median() * sc), "%.3g" % (a0.median() * sc)])
    P("Медианы здесь — по признакам, не по P&L.\n")
    P(md(rows, ["признак", "n стопов", "среднее: стопы", "среднее: остальные", "медиана: стопы", "медиана: остальные"]))
    rows = []
    for nm, m in (("до 24 ч назад: монета +10 % и больше за 24 ч", L["coin_r24h"] >= 0.10), ("монета −10 % и меньше за 24 ч", L["coin_r24h"] <= -0.10),
                  ("листинг внутри окна", L["new_list"] == 1), ("листинг ≤ 14 сут. до сигнала", (L["new_list"] == 1) & (L["age_d"] <= 14)), ("ранг оборота > 300", L["turn_rank"] > 300),
                  ("ранг оборота ≤ 100", L["turn_rank"] <= 100), ("NATR > 80 бп", L["natr_bp"] > 80), ("NATR ≤ 30 бп", L["natr_bp"] <= 30)):
        x = L[m]
        rows.append([nm, len(x), int(x["is_stop"].sum()), "%.1f%%" % (100 * x["is_stop"].mean()) if len(x) else "—", "%+.1f" % x[x["is_stop"]]["gf"].sum(), cell(x, False)])
    P("\nОбщая доля стопов в L_base — %.1f%% (%d из %d).\n" % (100 * L["is_stop"].mean(), L["is_stop"].sum(), len(L)))
    P(md(rows, ["срез", "n", "стопов", "доля стопов", "Σ стопов $", "все сделки среза: n / Σ / $ на сделку"]))

    # ---- D. предсказуемость: AUC ---------------------------------------------------------------------------------------------
    P("\n## D. Предсказуемость на входе: AUC причинных признаков (событие → признак)\n")
    P("AUC = P(признак у события > признак у остальных); 0,5 — нет сигнала. Интервал и p — круговой блочный бутстреп по суткам (блок 4, %d копий); "
      "Холм — по числу признаков в таблице (%d). Три выборки: (1) L_base kind=stop; (2) все сигналы L_sig — событие «цена ушла на −5 %% и ниже за 60 мин от входа» (без выходов движка); "
      "(3) то же для S_sig (+5 %%). Выборка (2)–(3) больше событий — больше мощности." % (NB, len(FEATS)))
    Ls, Ss = T[T["set"] == "L_sig"].copy(), T[T["set"] == "S_sig"].copy()
    P("\nСобытий: L_base stop %d из %d; L_sig ev5 %d из %d; S_sig ev5 %d из %d.\n" % (L["is_stop"].sum(), len(L), Ls["ev5"].sum(), len(Ls), Ss["ev5"].sum(), len(Ss)))
    res = {}
    for nm, d, ev in (("L_base stop", L, "is_stop"), ("L_sig ev5", Ls, "ev5"), ("S_sig ev5", Ss, "ev5")):
        out = []
        for var, _, _ in FEATS:
            if var not in d:
                out.append((np.nan,) * 4)
                continue
            out.append(boot_auc(d, var, ev))
        hp = holm([x[3] for x in out])
        res[nm] = (out, hp)
    rows = []
    for i, (var, nm, sc) in enumerate(FEATS):
        r = [nm]
        for k in ("L_base stop", "L_sig ev5", "S_sig ev5"):
            (a0, lo, hi, p), hp = res[k][0][i], res[k][1][i]
            r.append("—" if np.isnan(a0) else "%.3f [%.3f; %.3f] p_Holm=%.3f" % (a0, lo, hi, hp))
        rows.append(r)
    P(md(rows, ["признак", "L_base stop", "L_sig ev5 (−5 % за 60 мин)", "S_sig ev5 (+5 % за 60 мин)"]))

    # ---- E. корзины главных признаков ----------------------------------------------------------------------------------------
    P("\n## E. Доля стопов по корзинам (L_base; квартили) и помесячно\n")
    for var, nm, sc in FEATS:
        if var in ("new_list", "dow", "hour_utc", "fund_rate", "n_members"):
            continue
        v = L[var]
        if v.dropna().nunique() < 4:
            continue
        lab, order = bins_of(v, v, None, 4, "%.3g", sc)
        d = L.assign(_b=lab)
        rows = []
        for b in order:
            x = d[d["_b"] == b]
            if not len(x):
                continue
            rows.append([b, len(x), int(x["is_stop"].sum()), "%.1f%%" % (100 * x["is_stop"].mean()), "%+.1f" % x[x["is_stop"]]["gf"].sum(), "%+.1f" % x["gf"].sum()] +
                        ["%d/%d" % (x[(x["month"] == m)]["is_stop"].sum(), (x["month"] == m).sum()) for m in MONTHS])
        P("**%s**\n" % nm + md(rows, ["корзина", "n", "стопов", "доля", "Σ стопов $", "Σ всех $"] + ["%s стопов/n" % m for m in MONTHS]) + "\n")
    d = L.assign(hb=pd.cut(L["hour_utc"], [-1, 5, 11, 17, 23], labels=["00–06", "06–12", "12–18", "18–24"]).astype(str))
    rows = []
    for b in ("00–06", "06–12", "12–18", "18–24"):
        x = d[d["hb"] == b]
        rows.append([b, len(x), int(x["is_stop"].sum()), "%.1f%%" % (100 * x["is_stop"].mean()), "%+.1f" % x[x["is_stop"]]["gf"].sum(), "%+.1f" % x["gf"].sum()])
    P("**час входа UTC (владельцу GMT+4: +4 ч)**\n" + md(rows, ["часы", "n", "стопов", "доля", "Σ стопов $", "Σ всех $"]) + "\n")
    rows = []
    for c, g in L.groupby("sym"):
        if g["is_stop"].sum() >= 2:
            rows.append([c, len(g), int(g["is_stop"].sum()), "%+.1f" % g[g["is_stop"]]["gf"].sum(), "%+.1f" % g["gf"].sum()])
    P("**монеты с ≥ 2 стопами**\n" + (md(rows, ["монета", "сделок", "стопов", "Σ стопов $", "Σ всех $"]) if rows else "нет") + "\n")
    dd = L.groupby("abs_day").agg(n=("gf", "size"), st=("is_stop", "sum"), s=("gf", "sum"))
    dd = dd[dd["st"] >= 2].sort_index()
    rows = [[(pd.Timestamp("2026-07-01") + pd.Timedelta(days=int(i))).strftime("%m-%d"), int(r.n), int(r.st), "%+.1f" % r.s] for i, r in dd.iterrows()]
    P("**сутки с ≥ 2 стопами**\n" + (md(rows, ["день", "сделок", "стопов", "Σ $"]) if rows else "нет") + "\n")

    # ---- F. скорость −5 % -----------------------------------------------------------------------------------------------------
    P("\n## F. Как быстро пришёл стоп (L_base stop; по ленте от входа)\n")
    q = st_l["hold_s"].quantile([.1, .25, .5, .75, .9]).values / 60
    P("Удержание до стопа, мин (квантили .1/.25/.5/.75/.9 — по длительности, не по P&L): %s; среднее %.1f мин.\n" % (" / ".join("%.1f" % v for v in q), st_l["hold_s"].mean() / 60))
    rows = []
    for nm, d in (("стопы L_base", st_l), ("остальные L_base", L[~L["is_stop"]]), ("S_base stop", S[S["is_stop"]])):
        rows.append([nm, len(d)] + ["%+.1f" % d["mo_%d" % h].mean() for h in (1, 5, 15, 60)] + ["%.2f" % d["mfe_n"].mean(), "%.0f%%" % (100 * d["touch_h"].mean()), "%.1f" % (d["t_mae_h"].mean() / 60000)])
    P(md(rows, ["группа", "n", "ход через 1 мин, бп", "5 мин", "15 мин", "60 мин", "MFE NATR (среднее)", "доля, коснувшихся уровня", "момент MAE, мин"]))
    tt = st_l[st_l["touch_h"] == 1]
    P("\nСтопы, которые коснулись уровня до стопа: %d из %d. Среди остальных L_base касание: %.0f%%.\n" % (len(tt), len(st_l), 100 * L[~L["is_stop"]]["touch_h"].mean()))

    # ---- G. уровень стопа 5 % против MAE -------------------------------------------------------------------------------------
    P("\n## G. Уровень стопа −5 %: MAE выигрышей против стопа\n")
    P("MAE по ленте за удержание (бп от цены входа, ≤ 0). Если бы стоп стоял на −k %: сколько выигрышей и Σ$ лежат глубже k (их пришлось бы выбить), сколько убытков глубже k (их урезало бы). "
      "Это замер распределения, не кандидат: выход после первого тейка, трейл и остальные ветки движка не пересчитаны.\n")
    rows = []
    for st, d in (("L_base", L), ("S_base", S)):
        nst = d[~d["is_stop"]]
        win, los = nst[nst["gf"] > 0], nst[nst["gf"] <= 0]
        for k in (100, 200, 300, 400, 450):
            w, l = win[win["mae_h"] <= -k], los[los["mae_h"] <= -k]
            rows.append([st, "%.1f%%" % (k / 100), "%d из %d" % (len(w), len(win)), "%+.1f" % w["gf"].sum(), "%d из %d" % (len(l), len(los)), "%+.1f" % l["gf"].sum()])
    P(md(rows, ["ряд", "k (стоп на −k %)", "выигрыши глубже k: n", "Σ $ этих выигрышей", "невыигрыши (не стопы) глубже k: n", "Σ $ этих"]))
    rows = []
    for st, d in (("L_base", L), ("S_base", S)):
        d = d.assign(ratio=500 / d["natr_bp"])
        lab, order = bins_of(d["ratio"], d["ratio"], [4, 8, 12, 20], 4, "%.0f", 1)
        d = d.assign(_b=lab)
        for b in order:
            x = d[d["_b"] == b]
            if len(x):
                rows.append([st, b, len(x), int(x["is_stop"].sum()), "%.1f%%" % (100 * x["is_stop"].mean()), "%+.1f" % x[x["is_stop"]]["gf"].sum(), "%+.1f" % x["gf"].sum()])
    P("\nСтоп 5 % в единицах NATR сигнала (5 %/NATR; чем меньше, тем теснее для монеты):\n")
    P(md(rows, ["ряд", "5 %/NATR", "n", "стопов", "доля", "Σ стопов $", "Σ всех $"]))

    # ---- H. асимметрия: сырой ход против стороны -----------------------------------------------------------------------------
    P("\n## H. Асимметрия «против» по сырым сигналам (L_sig / S_sig; без выходов движка)\n")
    rows = []
    for st, d in (("L_sig", Ls), ("S_sig", Ss), ("L_base", L), ("S_base", S)):
        for m in MONTHS + ["all"]:
            x = d[(d["month"] == m)] if m != "all" else d
            if not len(x):
                continue
            col = "mae_60" if st.endswith("sig") else "mae_h"
            rows.append([st, m, len(x), "%.1f%%" % (100 * (x[col] <= -300).mean()), "%.1f%%" % (100 * (x[col] <= -500).mean()),
                         "%.1f%%" % (100 * (x[col.replace("mae", "mfe")] >= 300).mean()), "%.1f%%" % (100 * (x[col.replace("mae", "mfe")] >= 500).mean())])
    P("Для sig — за 60 мин от входа; для base — за удержание сделки. Доля сделок, у которых цена уходила против ≥ 3 % / ≥ 5 %, и в пользу ≥ 3 % / ≥ 5 % (симметрично: шорт — вверх против).\n")
    P(md(rows, ["ряд", "месяц", "n", "против ≥ 3 %", "против ≥ 5 %", "в пользу ≥ 3 %", "в пользу ≥ 5 %"]))

    # ---- I. ретест-подмножество -----------------------------------------------------------------------------------------------
    P("\n## I. Что представляет подмножество «ретест» (L_base: заполнился у L_mk2 или нет)\n")
    rows = []
    for nm, x in (("заполнился (237)", L[L["mk2_fill"] == 1]), ("не заполнился (384)", L[L["mk2_fill"] == 0])):
        rows.append([nm, cell(x), "%d" % x["is_stop"].sum(), "%+.1f" % x[x["is_stop"]]["gf"].sum(), ", ".join("%s %d / %+.0f" % (k, len(g), g["gf"].sum()) for k, g in x.groupby("kind")),
                     "%.0f%%" % (100 * x["touch_h"].mean()), "%.0f%%" % (100 * (x["pierce_min"] == 1).mean()), "%.0f%%" % (100 * (x["retest_min"] == 1).mean())])
    P(md(rows, ["группа", "n / Σ / $ на сделку [CI]", "стопов", "Σ стопов $", "выходы: n / Σ", "касание уровня за удержание", "пробой в минуте сигнала (прокси)", "ретест (прокси)"]))
    L["fillv"] = L["mk2_fill"] == 1
    out = [boot_auc(L, var, "fillv") for var, _, _ in FEATS]
    hp = holm([x[3] for x in out])
    rows = [[nm, "%.3f [%.3f; %.3f]" % (x[0], x[1], x[2]) if not np.isnan(x[0]) else "—", "%.3f" % h if not np.isnan(h) else "—"] for (var, nm, _), x, h in zip(FEATS, out, hp)]
    P("\nAUC «заполнился» против «не заполнился» по причинным признакам (Холм по %d признакам):\n" % len(FEATS))
    P(md(rows, ["признак", "AUC [95 %]", "p по Холму"]))
    rows = []
    for m in MONTHS:
        x = L[L["month"] == m]
        f, n = x[x["mk2_fill"] == 1], x[x["mk2_fill"] == 0]
        rows.append([m, len(x), "%d / %+.1f" % (len(f), f["gf"].sum()), "%d / %+.1f" % (len(n), n["gf"].sum()), "%d" % f["is_stop"].sum(), "%d" % n["is_stop"].sum()])
    P("\nПомесячно (n / Σ $ тейкера):\n")
    P(md(rows, ["месяц", "сделок", "заполнились", "не заполнились", "стопов среди заполнившихся", "стопов среди не заполнившихся"]))

    open(a.out, "w", encoding="utf-8").write("\n".join(o) + "\n")
    print("written", a.out, len(o))


if __name__ == "__main__":
    main()
