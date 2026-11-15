"""Таблицы отчёта «сделки BingX как цена» (markdown, десятичная запятая) из bx_trades_run.py (run.pkl) и bx_snap_check.py (check.pkl).

Запуск: python3 bx_trades_report.py <run.pkl> [check.pkl] [sample.json] > report-blocks.md
Ярусы — по медианной паузе между пачками сделок BingX у монеты (измерено): A ≤ 2,2 с, B 2,2–7 с, C 7–12 с, D ≥ 12 с.
"""
import json
import pickle
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
import bx_trades as bx  # noqa: E402

TIERS = [("A", 0, 2200), ("B", 2200, 7000), ("C", 7000, 12000), ("D", 12000, 1e18)]
TIER_NAME = {"A": "A: пауза ≤ 2,2 с", "B": "B: 2,2–7 с", "C": "C: 7–12 с", "D": "D: ≥ 12 с"}
TH_LABEL = {20.0: "0,2", 30.0: "0,3", 50.0: "0,5", 100.0: "1,0"}


def f(x, n=1):
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return "—"
    if isinstance(x, float) and np.isinf(x):
        return "∞"
    return f"{x:.{n}f}".replace(".", ",")


def pc(x, n=1):
    return "—" if x is None else f(100 * x, n) + " %"


def fms(ms):
    if ms is None or (isinstance(ms, float) and np.isnan(ms)):
        return "—"
    if np.isinf(ms):
        return "∞"
    if ms < 1000:
        return f(ms, 0) + " мс"
    if ms < 60_000:
        return f(ms / 1000, 1) + " с"
    return f(ms / 60_000, 1) + " мин"


def table(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    out += ["| " + " | ".join(str(c) for c in r) + " |" for r in rows]
    return "\n".join(out)


def tier_of(g):
    for n, lo, hi in TIERS:
        if lo <= g < hi:
            return n


def qs(v, p):
    v = np.asarray(v, float)
    v = v[~np.isnan(v)]
    return float(np.percentile(v, p)) if len(v) else None


def hist_q(h, edges, q):
    """Квантиль по гистограмме с границами edges (середина корзины)."""
    c = np.cumsum(h)
    if not c[-1]:
        return None
    i = int(np.searchsorted(c, q * c[-1]))
    return float((edges[i] + edges[i + 1]) / 2)


def cadence(R):
    out = ["### 1. Как часто BingX печатает сделки (по монетам)\n"]
    rows, all_gap = [], []
    by = {t: [] for t, _, _ in TIERS}
    for coin, r in R["coins"].items():
        by[tier_of(r["stats"]["gap_med"])].append((coin, r))
    hours = (R["t1"] - R["t0"]) / 3_600_000
    for t, _, _ in TIERS:
        lst = by[t]
        if not lst:
            continue
        st = [r["stats"] for _, r in lst]
        g = [s["gap_med"] for s in st]
        h = np.sum([s["gap_hist"] for s in st], axis=0)
        w = np.array([s["n_times"] for s in st], float)
        rows.append([TIER_NAME[t], len(lst), fms(np.percentile(g, 10)) + " … " + fms(np.percentile(g, 90)), f(np.median([s["n_trades"] / hours for s in st]), 0),
                     pc(np.average([s["reg10"] for s in st], weights=w)), fms(hist_q(h, bx.GAP_EDGES, 0.5)), fms(hist_q(h, bx.GAP_EDGES, 0.9)),
                     pc(np.mean([s["stale"][1] for s in st])), pc(np.mean([s["stale"][2] for s in st])), pc(np.mean([s["stale"][3] for s in st])),
                     f(np.median([s["usd_med"] for s in st]), 1)])
    out.append(table(["ярус (по медианной паузе)", "монет", "медианная пауза: p10 … p90 по монетам", "сделок в час (медиана по монетам)", "пауз в ±10 % от медианной",
                      "пауза p50 (все паузы яруса)", "p90", "доля окна, где последняя сделка старше 1 с", "10 с", "60 с", "медиана объёма пачки, $"], rows))
    top = sorted(R["coins"].items(), key=lambda kv: kv[1]["stats"]["gap_med"])[:12]
    out.append("\nСамые частые (медианная пауза): " + "; ".join(f"{c} {fms(r['stats']['gap_med'])} ({r['stats']['n_trades'] / hours:,.0f}/ч)".replace(",", " ") for c, r in top) + ".")
    return "\n".join(out), by


def estimator(C, sample):
    out = ["### 2. Оценка верха BingX по сделкам против настоящих снимков bookTicker (выборка монет, все снимки)\n"]
    res = C["res"]
    by = {}
    for sym, r in res.items():
        by.setdefault(sample[sym]["tier"][0], []).append(r)
    rows, rows2 = [], []
    for t, _, _ in TIERS:
        lst = by.get(t, [])
        if not lst:
            continue

        def agg(key):
            n = sum(r["cons"][key]["n"] for r in lst)
            return n, {k: sum(r["cons"][key][k] for r in lst) for k in ("inside", "eq_prev", "eq_next", "below", "above")}, np.sum([r["cons"][key]["hist"] for r in lst], axis=0)
        n, a, h = agg("all")
        hh = h.astype(float)
        c = np.cumsum(hh)
        lg = lambda q: (10 ** (bx_log_lo() + 0.1 * (np.searchsorted(c, q * c[-1]) + 0.5))) if c[-1] else None  # noqa: E731
        nb, ab, _ = agg("buy")
        ns, as_, _ = agg("sell")
        n1, a1, _ = agg("single")
        n2, a2, _ = agg("sweep")
        rows.append([TIER_NAME[t], len(lst), f(np.median([r["snap_gap_ms"] for r in lst]), 0) + " мс", f(np.median([r["spread_med_pct"] for r in lst]), 3) + " %", n,
                     pc(a["inside"] / n), pc(a["eq_prev"] / n), pc(a["eq_next"] / n), pc(ab["inside"] / nb), pc(as_["inside"] / ns), pc(a1["inside"] / n1), pc(a2["inside"] / n2) if n2 else "—",
                     pc(a["below"] / n), pc(a["above"] / n), f(lg(0.5), 3) + " / " + f(lg(0.9), 3) + " %" if lg(0.5) else "—"])
        p = {}
        for nm in ("ask", "bid", "mid"):
            for sub in ("all", "changed"):
                p[(nm, sub)] = {k: sum(r["pred"][nm][sub][k] for r in lst) for k in ("n", "exact_est", "exact_base", "better", "worse")}
                for kk in ("hist_est", "hist_base"):
                    p[(nm, sub)][kk] = np.sum([r["pred"][nm][sub][kk] for r in lst], axis=0)
        n_int = sum(r["pred"]["n_int"] for r in lst)
        n_ti = sum(r["pred"]["n_trade_int"] for r in lst)

        def lq(hh, ex, q):
            hh = np.asarray(hh, float)
            tot = hh.sum() + ex
            if not tot:
                return None
            cc = ex + np.cumsum(hh)
            i = int(np.searchsorted(cc, q * tot))
            return 0.0 if q * tot <= ex else 10 ** (bx_log_lo() + 0.1 * (i + 0.5))
        for nm in ("ask", "bid"):
            x = p[(nm, "changed")]
            rows2.append([TIER_NAME[t], nm, pc(n_ti / n_int), x["n"], pc(x["exact_est"] / x["n"]) if x["n"] else "—", pc(x["exact_base"] / x["n"]) if x["n"] else "—",
                          f(lq(x["hist_est"], x["exact_est"], 0.5), 4) + " %" if x["n"] else "—", f(lq(x["hist_base"], x["exact_base"], 0.5), 4) + " %" if x["n"] else "—",
                          pc(x["better"] / x["n"]) if x["n"] else "—", pc(x["worse"] / x["n"]) if x["n"] else "—"])
    out.append("Согласованность каждой пачки: цена первой сделки в пределах [min, max] того же края (ask — покупка, bid — продажа) двух снимков, между которыми она прошла.\n")
    out.append(table(["ярус", "монет в выборке", "пауза между снимками, медиана", "спред BingX, медиана", "пачек", "внутри [min, max] двух снимков", "= предыдущему снимку", "= следующему", "покупки внутри", "продажи внутри",
                      "одиночные внутри", "пачки с несколькими ценами внутри", "ниже обоих", "выше обоих", "ошибка вне диапазона, медиана / p90"], rows))
    out.append("\nПредсказание: оценка = снимок k−1 + сделки в (T_{k−1}, T_k]; против снимка k; только интервалы, где снимок изменился («изменился»); база — снимок k−1 без сделок.\n")
    out.append(table(["ярус", "край", "доля интервалов со сделкой", "интервалов (край изменился, была сделка этой стороны)", "оценка точна", "база точна", "ошибка оценки, медиана", "ошибка базы, медиана", "оценка лучше базы", "оценка хуже базы"], rows2))
    rows3 = []
    import bx_snap_check as sc
    for t, _, _ in TIERS:
        lst = by.get(t, [])
        if not lst:
            continue
        for nm, ru in (("покупка (ask)", "buy"), ("продажа (bid)", "sell")):
            cls = np.sum([r["cons"]["touch"][ru]["cls"] for r in lst], axis=0)
            n = int(cls.sum())
            if not n:
                continue
            h = np.sum([r["cons"]["touch"][ru]["imp_hist"] for r in lst], axis=0)
            rows3.append([TIER_NAME[t], nm, n, pc(cls[0] / n), pc(cls[1] / n), pc(cls[2] / n), pc(cls[3] / n), pc(cls[4] / n), f(hist_q(h, sc.TOUCH_EDGES, 0.5), 3) + " %" if h.sum() else "—"])
    out.append("\nКасание: пачки, у которых верх снимка (bid и ask) не менялся между двумя снимками вокруг неё — цена первой сделки против bid и ask снимка:\n")
    out.append(table(["ярус", "сторона агрессора", "пачек", "ниже bid", "= bid", "внутри спреда", "= ask", "выше ask", "внутри спреда: улучшение против касания своей стороны, медиана"], rows3))
    return "\n".join(out)


def bx_log_lo():
    import bx_snap_check
    return bx_snap_check.LOG_LO


def leadlag(R, by):
    out = ["### 3. Лидерство: движение Binance/Bybit/OKX → ответ BingX\n"]
    tier = {c: t for t, lst in by.items() for c, _ in lst}
    acc, accf = {}, {}
    for coin, r in R["coins"].items():
        for ex, fd in r["F"].items():
            for x, a in fd["ll"].items():
                if len(a):
                    acc.setdefault((tier[coin], x), []).append(a)
                    accf.setdefault((ex, x), []).append(a)

    def row(a):
        led = a[:, 2] == 1
        nl = a[~led]
        ns = nl[nl[:, 8] == 1]
        rt, rs = ns[:, 3], nl[:, 4]
        return [len(a), pc(led.mean()), len(nl), pc(len(ns) / max(len(nl), 1), 0)] + [pc(np.mean(rt <= h)) for h in (300, 1000, 3000, 10_000, 60_000)] + [pc(np.mean(~np.isnan(rt)))]             + [fms(qs(rt, 50)), fms(qs(rt, 90))] + [pc(np.mean(rs <= h)) for h in (300, 1000)] + [fms(qs(rs, 50)), fms(qs(rs, 90))]
    head = ["сдвиг F ≥", "ярус", "событий", "BingX уже там (led)", "остальных", "из них с опорой по сделкам", "сделка ≤ 0,3 с", "≤ 1 с", "≤ 3 с", "≤ 10 с", "≤ 60 с", "хоть когда за 10 мин", "лаг сделки p50", "p90",
            "снимок ≤ 0,3 с", "≤ 1 с", "лаг снимка p50", "p90"]
    rows = []
    for x in bx.XS:
        for t, _, _ in TIERS:
            if (t, x) in acc:
                rows.append([f(100 * x, 1) + " %", TIER_NAME[t]] + row(np.concatenate(acc[(t, x)])))
        rows.append([f(100 * x, 1) + " %", "все"] + row(np.concatenate([np.concatenate(v) for k, v in acc.items() if k[1] == x])))
    out.append(table(head, rows))
    rows = []
    for x in bx.XS:
        for ex in ("binance", "bybit", "okx"):
            if (ex, x) in accf:
                rows.append([f(100 * x, 1) + " %", ex] + row(np.concatenate(accf[(ex, x)])))
    out.append("\nПо быстрой бирже (все ярусы):\n")
    out.append(table([h if h != "ярус" else "биржа" for h in head], rows))
    return "\n".join(out)


def day_labels(R):
    e = R["days"]
    lab = []
    for a, b in zip(e[:-1], e[1:]):
        d = datetime.fromtimestamp((a + 4 * 3600_000) / 1000, timezone.utc)
        lab.append(f"{d.day:02d}.{d.month:02d} ({f((b - a) / 3_600_000, 1)} ч)")
    return lab


def divergence(R, by, mode):
    nd = len(R["days"]) - 1
    tier = {c: t for t, lst in by.items() for c, _ in lst}
    labs = day_labels(R)
    out = []
    groups = {t: [] for t, _, _ in TIERS}
    for coin, r in R["coins"].items():
        for ex, fd in r["F"].items():
            groups[tier[coin]].append(fd["div"][mode])
    rows, rows_d = [], []
    hours = (R["t1"] - R["t0"]) / 3_600_000
    for th in bx.THETAS:
        for t in [x[0] for x in TIERS] + ["все"]:
            gs = [g for tt, lst in groups.items() for g in lst] if t == "все" else groups[t]
            nv = sum(g["n_valid"].sum() for g in gs)
            nq = sum(g["q"][(th, 1)][0].sum() + g["q"][(th, -1)][0].sum() for g in gs)
            usd = sum(g["q"][(th, 1)][1].sum() + g["q"][(th, -1)][1].sum() for g in gs)
            eps = [np.vstack([g["eps"][(th, 1)], g["eps"][(th, -1)]]) for g in gs]
            pairs_ep = sum(1 for e in eps if len(e))
            E = np.vstack(eps) if eps else np.zeros((0, 8))
            lab = [TH_LABEL[th] + " %", TIER_NAME.get(t, t)]
            if not len(E):
                rows.append(lab + [len(gs), nv, nq, "0"] + ["—"] * 6)
                rows_d.append(lab + ["—"] * 9)
                continue
            D = np.minimum(E[:, 2], E[:, 3])
            fin = lambda v: v[~np.isinf(v)]  # noqa: E731
            rows.append(lab + [len(gs), nv, nq, f(len(E) / (hours / 24), 0), pc(pairs_ep / len(gs)), pc(nq / nv, 3), f(np.median(E[:, 1]) / 100, 2) + " %", f(np.median(E[:, 4]), 0),
                               f(usd / (hours / 24), 0), pc(np.nanmean(E[:, 6] >= th), 0)])
            rows_d.append(lab + [fms(qs(fin(D), 50)), fms(qs(np.where(np.isinf(D), 1e12, D), 90)) if np.isinf(D).mean() < 0.1 else "> 10 мин", pc(np.isinf(D).mean()),
                                 fms(qs(fin(E[:, 2]), 50)), pc(np.isinf(E[:, 2]).mean()), fms(qs(fin(E[:, 3]), 50)), pc(np.isinf(E[:, 3]).mean()), pc(np.mean(E[:, 3] < E[:, 2])),
                                 f(np.nanmedian(E[:, 7]) / 100, 3) + " %"])
    out.append(table(["θ", "ярус", "пар (монета × F)", "пачек с известной F", "подходящих пачек", "эпизодов в сутки", "пар с ≥ 1 эпизодом", "доля подходящих пачек", "раздвижка на старте, медиана",
                      "объём пачки на старте, $ (медиана)", "объём подходящих пачек, $ в сутки (по каждой F отдельно)", "по снимку BingX на тот же момент тоже ≥ θ"], rows))
    out.append("\nДлительность эпизода: D = min(D_F, D_B); D_F — быстрая книга сошлась при неизменной цене BingX; D_B — следующая пачка BingX (любая сторона) показала цену, при которой раздвижка < θ.\n")
    out.append(table(["θ", "ярус", "D p50 (по сошедшимся)", "D p90", "D: не сошлись за 10 мин", "D_F p50", "D_F не сошлась", "D_B p50", "D_B не было", "сначала сдвинулся BingX (D_B < D_F)",
                      "цена сделки лучше касания снимка, медиана"], rows_d))
    # по суткам (все ярусы)
    rows = []
    gs = [g for lst in groups.values() for g in lst]
    for th in bx.THETAS:
        r = [TH_LABEL[th] + " %"]
        for d in range(nd):
            n = sum(g["q"][(th, 1)][0][d] + g["q"][(th, -1)][0][d] for g in gs)
            v = sum(g["n_valid"][:, d].sum() for g in gs)
            ep = sum(int((bx.day_idx(g["eps"][(th, s)][:, 0].astype(np.int64), R["days"]) == d).sum()) for g in gs for s in (1, -1) if len(g["eps"][(th, s)]))
            r.append(f"{ep} эп. / {n} пачек ({pc(n / v, 3)})")
        rows.append(r)
    out.append("\nПо суткам GMT+4 (все ярусы; эпизодов / подходящих пачек, доля):\n")
    out.append(table(["θ"] + labs, rows))
    return "\n".join(out)


def snap_div(R, by, mode):
    nd = len(R["days"]) - 1
    tier = {c: t for t, lst in by.items() for c, _ in lst}
    groups = {t: [] for t, _, _ in TIERS}
    for coin, r in R["coins"].items():
        for ex, fd in r["F"].items():
            groups[tier[coin]].append(fd["snap"][mode])
    hours = (R["t1"] - R["t0"]) / 3_600_000
    rows, rows_c = [], []
    BK = [0, 400, 1000, 10_000, 60_000, 1e18]
    BN = ["< 0,4 с", "0,4–1 с", "1–10 с", "10–60 с", "≥ 1 мин"]
    for th in bx.THETAS:
        for t in [x[0] for x in TIERS] + ["все"]:
            gs = [g for tt, lst in groups.items() for g in lst] if t == "все" else groups[t]
            runs = [np.vstack([g[(th, 1)]["runs"], g[(th, -1)]["runs"]]) for g in gs]
            Rn = np.vstack(runs) if runs else np.zeros((0, 4))
            tshare = np.mean([(g[(th, 1)]["time"] + g[(th, -1)]["time"]) for g in gs])
            if not len(Rn):
                rows.append([TH_LABEL[th] + " %", TIER_NAME.get(t, t), len(gs), "0", "—", "—", "—", "—", "—", "—"])
                continue
            cls = np.bincount(Rn[:, 2].astype(int), minlength=3) / len(Rn)
            rows.append([TH_LABEL[th] + " %", TIER_NAME.get(t, t), len(gs), f(len(Rn) / (hours / 24), 0), pc(np.mean([len(r) > 0 for r in runs])), pc(tshare, 3),
                         fms(qs(Rn[:, 1], 50)), fms(qs(Rn[:, 1], 90)), pc(cls[0]) + " / " + pc(cls[1]) + " / " + pc(cls[2])])
            if t == "все":
                row = [TH_LABEL[th] + " %"]
                for a, b in zip(BK[:-1], BK[1:]):
                    m = (Rn[:, 1] >= a) & (Rn[:, 1] < b)
                    if m.sum():
                        c3 = np.bincount(Rn[m, 2].astype(int), minlength=3) / m.sum()
                        row.append(f"{int(m.sum())}: {pc(c3[0], 0)} / {pc(c3[1], 0)} / {pc(c3[2], 0)}")
                    else:
                        row.append("0")
                rows_c.append(row)
    out = [table(["θ", "ярус", "пар", "эпизодов (снимки) в сутки", "пар с ≥ 1 эпизодом", "доля времени (среднее по парам)", "длительность p50", "p90", "подтверждён сделкой / опровергнут / без проверки"], rows)]
    out.append("\nЭпизоды по снимкам по длительности (число: подтверждён / опровергнут / без проверки):\n")
    out.append(table(["θ"] + BN, rows_c))
    rows = []
    for th in bx.THETAS:
        r = [TH_LABEL[th] + " %"]
        gs = [g for lst in groups.values() for g in lst]
        for d in range(nd):
            n = sum(int((g[(th, s)]["runs"][:, 3] == d).sum()) for g in gs for s in (1, -1) if len(g[(th, s)]["runs"]))
            r.append(str(n))
        rows.append(r)
    out.append("\nЭпизоды по снимкам по суткам GMT+4:\n")
    out.append(table(["θ"] + day_labels(R), rows))
    return "\n".join(out)


def examples(R, by, th=100.0, k=12):
    tier = {c: t for t, lst in by.items() for c, _ in lst}
    best = {}
    for coin, r in R["coins"].items():
        for ex, fd in r["F"].items():
            for s in (1, -1):
                E = fd["div"]["c"]["eps"][(th, s)]
                if len(E):
                    best[(coin, ex, s)] = (E[int(np.argmax(E[:, 1]))], len(E))
    out = []
    for (coin, ex, s), (e, n) in sorted(best.items(), key=lambda kv: -kv[1][0][1])[:k]:
        ts = datetime.fromtimestamp(e[0] / 1000 + 4 * 3600, timezone.utc).strftime("%d.%m %H:%M:%S")
        out.append([coin, tier[coin], ex, "BingX дешевле" if s > 0 else "BingX дороже", n, ts, f(e[1] / 100, 2) + " %", fms(e[2]), fms(e[3]), f(e[4], 0)])
    return table(["монета", "ярус", "F", "направление", "эпизодов ≥ θ у пары за окно", "время максимума, GMT+4", "раздвижка к центру", "D_F", "D_B", "объём пачки, $"], out)


def per_exchange(R, mode="c"):
    rows = []
    hours = (R["t1"] - R["t0"]) / 3_600_000
    for ex in ("binance", "bybit", "okx"):
        gs = [fd["div"][mode] for r in R["coins"].values() for e, fd in r["F"].items() if e == ex]
        sn = [fd["snap"][mode] for r in R["coins"].values() for e, fd in r["F"].items() if e == ex]
        for th in bx.THETAS:
            E = np.vstack([np.vstack([g["eps"][(th, 1)], g["eps"][(th, -1)]]) for g in gs])
            runs = sum(len(g[(th, 1)]["runs"]) + len(g[(th, -1)]["runs"]) for g in sn)
            D = np.minimum(E[:, 2], E[:, 3])
            rows.append([ex, TH_LABEL[th] + " %", len(gs), f(len(E) / (hours / 24), 0), f(runs / (hours / 24), 0), fms(qs(D[~np.isinf(D)], 50)) if len(E) else "—", pc(np.isinf(D).mean()) if len(E) else "—"])
    return table(["F", "θ", "пар", "эпизодов по сделкам в сутки", "эпизодов по снимкам в сутки", "D p50", "D: не сошлись за 10 мин"], rows)


def main(run, check=None, sample=None):
    R = pickle.load(open(run, "rb"))
    print("# Блоки отчёта (автогенерация bx_trades_report.py)\n")
    s, by = cadence(R)
    print(s, "\n")
    if check:
        print(estimator(pickle.load(open(check, "rb")), json.load(open(sample))), "\n")
    print(leadlag(R, by), "\n")
    print("### 4a. Раздвижка по сделкам BingX — к центру (авто-центр viking)\n")
    print(divergence(R, by, "c"), "\n")
    print("### 4b. Раздвижка по сделкам BingX — от нуля\n")
    print(divergence(R, by, "0"), "\n")
    print("### 5a. Те же пары по снимкам BingX — к центру\n")
    print(snap_div(R, by, "c"), "\n")
    print("### 5b. Те же пары по снимкам BingX — от нуля\n")
    print(snap_div(R, by, "0"), "\n")
    print("### 6. Крупнейшие раздвижки по сделкам (θ = 1 %, к центру)\n")
    print(examples(R, by), "\n")
    print("### 7. По быстрой бирже (к центру)\n")
    print(per_exchange(R), "\n")


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None, sys.argv[3] if len(sys.argv) > 3 else None)
