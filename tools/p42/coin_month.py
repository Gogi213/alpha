"""П-42 v2: полная таблица метрик по стороне × монете × календарному месяцу и по портфелю × месяцу, в $ и в % (счёт $2 500, позиция $500).

Вызывается из make_stats.py: пишет docs/findings/p42-per-coin-month/{long,short,portfolio}.csv и возвращает раздел отчёта.
Движки: tick = tick-lat/b0_lx200 (основной), 1m = stage1/b0 (второй блок). Счёта нет — только разбор готовых рядов.
"""
import csv
import os

import numpy as np

import make_stats as ms

ACCT = 2500.0
POS = ms.NOTIONAL
ENG = (("tick", "tick-lat/b0_lx200", "тики, задержка 200 мс (основной)"), ("1m", "stage1/b0", "минутки (второй блок)"))
# (метка в CSV, название, первый индекс суток, после последнего, неполный)
PERIODS = (("2026-09p", "сентябрь 15–30.09 (16 сут., неполный)", 0, 16, True), ("2026-10", "октябрь (31 сут.)", 16, 47, False),
           ("2026-11p", "ноябрь 1–13.11 (13 сут., неполный)", 47, 60, True), ("ALL", "всё окно 15.09–13.11 (60 сут.)", 0, 60, False))
DIR = os.path.join(os.path.dirname(__file__), "..", "..", "docs", "findings", "p42-per-coin-month")
COLS = ["side", "engine", "coin", "month", "days", "partial", "trades", "trades_per_day", "win_rate_pct", "avg_win_usd", "avg_loss_usd", "avg_trade_usd",
        "avg_trade_bps", "profit_factor", "total_usd", "usd_per_day", "max_drawdown_usd", "longest_no_new_high_days", "no_new_high_tail_days",
        "worst_day_usd", "best_day_usd", "stop_pct", "breakeven_pct", "trail_pct", "all_takes_pct", "avg_hold_min", "median_hold_min",
        "total_return_pct_acct", "usd_per_day_pct_acct", "max_drawdown_pct_peak", "worst_day_pct_acct", "best_day_pct_acct",
        "avg_win_pct_acct", "avg_loss_pct_acct", "avg_trade_pct_acct", "avg_win_pct_pos", "avg_loss_pct_pos", "avg_trade_pct_pos"]


def month_of(day):
    for lab, _, a, b, _ in PERIODS[:3]:
        if a <= day < b:
            return lab


def coinday(rel, side):
    out, act = {}, {}
    for r in csv.DictReader(open(os.path.join(ms.ROOT, rel, side + "-coinday.csv"), encoding="utf-8")):
        s, d = ms.fixname(r["sym"]), int(r["day"])
        out.setdefault(s, np.zeros(60))[d] += float(r["pnl"])
        act.setdefault(s, np.zeros(60, dtype=bool))[d] = True
    return out, act


def row(tr, series, active, nd):
    """tr — сделки периода (по дню входа), series — суточный P&L периода (nd суток), active — маска суток с P&L монеты (None — все)."""
    r = dict(n=len(tr), nd=nd, total=float(series.sum()))
    cum = np.cumsum(series)
    peak = 0.0
    dd = ddp = since = longest = 0.0
    for v in cum:
        if v > peak:
            peak, since = v, 0
        else:
            since += 1
        dd = max(dd, peak - v)
        ddp = max(ddp, (peak - v) / (ACCT + peak))
        longest = max(longest, since)
    r.update(dd=float(dd), ddp=float(ddp), longest=int(longest), tail=int(since))
    dayv = series if active is None else series[active]
    r["worst"] = float(dayv.min()) if len(dayv) else None
    r["best"] = float(dayv.max()) if len(dayv) else None
    if tr:
        net = np.array([t["net"] for t in tr])
        w, l = net[net > 0], net[net < 0]
        r.update(win=len(w) / len(tr), avg_win=float(w.mean()) if len(w) else None, avg_loss=float(l.mean()) if len(l) else None,
                 avg=float(net.mean()), pf=float(w.sum() / -l.sum()) if len(l) else None, trade_sum=float(net.sum()))
        ks = [ms.klass(t) for t in tr]
        for k in ("stop", "be", "trail", "take"):
            r["sh_" + k] = ks.count(k) / len(tr)
        h = np.array([t["hold"] for t in tr])
        r["hold"], r["hold_med"] = float(h.mean()), float(np.median(h))
    return r


def pc(x, k):
    return None if x is None else x / k * 100


def csv_row(side, eng, coin, lab, partial, r):
    n, nd = r["n"], r["nd"]
    g = lambda k: r.get(k)
    v = [side, eng, coin, lab, nd, int(partial), n, n / nd, pc(g("win"), 1), g("avg_win"), g("avg_loss"), g("avg"),
         None if g("avg") is None else g("avg") / POS * 1e4, g("pf"), r["total"], r["total"] / nd, r["dd"], r["longest"], r["tail"], r["worst"], r["best"],
         pc(g("sh_stop"), 1), pc(g("sh_be"), 1), pc(g("sh_trail"), 1), pc(g("sh_take"), 1), g("hold"), g("hold_med"),
         pc(r["total"], ACCT), pc(r["total"] / nd, ACCT), r["ddp"] * 100, pc(r["worst"], ACCT), pc(r["best"], ACCT),
         pc(g("avg_win"), ACCT), pc(g("avg_loss"), ACCT), pc(g("avg"), ACCT), pc(g("avg_win"), POS), pc(g("avg_loss"), POS), pc(g("avg"), POS)]
    return [("" if x is None else (round(x, 4) if isinstance(x, float) else x)) for x in v]


METRICS = [
    ("Сделок", lambda r: str(r["n"])),
    ("Сделок в сутки", lambda r: ms.f(r["n"] / r["nd"], 2)),
    ("Доля прибыльных, %", lambda r: ms.pct(r.get("win"))),
    ("Средняя прибыльная, $", lambda r: ms.f(r.get("avg_win"))),
    ("Средняя прибыльная, % от позиции $500", lambda r: ms.f(pc(r.get("avg_win"), POS))),
    ("Средняя прибыльная, % от счёта $2 500", lambda r: ms.f(pc(r.get("avg_win"), ACCT), 3)),
    ("Средняя убыточная, $", lambda r: ms.f(r.get("avg_loss"))),
    ("Средняя убыточная, % от позиции", lambda r: ms.f(pc(r.get("avg_loss"), POS))),
    ("Средняя убыточная, % от счёта", lambda r: ms.f(pc(r.get("avg_loss"), ACCT), 3)),
    ("Средняя сделка, $", lambda r: ms.f(r.get("avg"))),
    ("Средняя сделка, % от позиции", lambda r: ms.f(pc(r.get("avg"), POS))),
    ("Средняя сделка, % от счёта", lambda r: ms.f(pc(r.get("avg"), ACCT), 3)),
    ("Profit factor", lambda r: ms.f(r.get("pf"))),
    ("Итого, $", lambda r: ms.f(r["total"])),
    ("**Итого, % счёта (доходность)**", lambda r: ms.f(pc(r["total"], ACCT))),
    ("$ в сутки", lambda r: ms.f(r["total"] / r["nd"])),
    ("% счёта в сутки", lambda r: ms.f(pc(r["total"] / r["nd"], ACCT), 3)),
    ("Макс. просадка, $", lambda r: ms.f(r["dd"])),
    ("**Макс. просадка, % (от пика счёта)**", lambda r: ms.f(r["ddp"] * 100)),
    ("Дольше всего без нового максимума, сут. (хвост)", lambda r: "%d (%d)" % (r["longest"], r["tail"])),
    ("Худшие сутки, $ / % счёта", lambda r: "%s / %s" % (ms.f(r["worst"]), ms.f(pc(r["worst"], ACCT)))),
    ("Лучшие сутки, $ / % счёта", lambda r: "%s / %s" % (ms.f(r["best"]), ms.f(pc(r["best"], ACCT)))),
    ("Выход по стопу, %", lambda r: ms.pct(r.get("sh_stop"))),
    ("Выход по безубытку, %", lambda r: ms.pct(r.get("sh_be"))),
    ("Выход по трейлу, %", lambda r: ms.pct(r.get("sh_trail"))),
    ("Закрыто тейками (все исполнены), %", lambda r: ms.pct(r.get("sh_take"))),
    ("Среднее время в сделке, мин (медиана)", lambda r: "—" if "hold" not in r else "%s (%s)" % (ms.f(r["hold"], 0), ms.f(r["hold_med"], 1))),
]


def run():
    os.makedirs(DIR, exist_ok=True)
    rows = {s: [] for s in ms.SIDES}
    port = []
    pdata = {}
    diff = [0.0, 0.0]  # наибольшее |сумма P&L сделок − суточная сумма|: по портфелю×месяцу / по монете×месяцу
    coins = {}
    cm = {}
    for side in ms.SIDES:
        for eng, rel, _ in ENG:
            v = ms.load(rel, side)
            cd, act = coinday(rel, side)
            tr_all = v["trades"]
            for lab, _, a, b, part in PERIODS:
                tr = [t for t in tr_all if a <= t["day"] < b]
                r = row(tr, v["daily"][a:b], None, b - a)
                if tr:
                    diff[0] = max(diff[0], abs(r["trade_sum"] - r["total"]))
                port.append(csv_row(side, eng, "PORTFOLIO", lab, part, r))
                pdata[(side, eng, lab)] = r
            by = {}
            for t in tr_all:
                by.setdefault(t["sym"], []).append(t)
            for coin in sorted(set(by) | set(cd)):
                for lab, _, a, b, part in PERIODS:
                    tr = [t for t in by.get(coin, []) if a <= t["day"] < b]
                    ser = cd[coin][a:b] if coin in cd else np.zeros(b - a)
                    ac = act[coin][a:b] if coin in act else np.zeros(b - a, dtype=bool)
                    if not tr and not ac.any():
                        continue
                    r = row(tr, ser, ac, b - a)
                    if tr:
                        diff[1] = max(diff[1], abs(r["trade_sum"] - r["total"]))
                    rows[side].append(csv_row(side, eng, coin, lab, part, r))
                    cm[(side, eng, coin, lab)] = r
                    if lab == "ALL":
                        coins[(side, eng, coin)] = r
    for name, data in (("long", rows["long"]), ("short", rows["short"]), ("portfolio", port)):
        with open(os.path.join(DIR, name + ".csv"), "w", encoding="utf-8-sig", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(COLS)
            w.writerows(data)
    return pdata, coins, diff, {k: len(v) for k, v in rows.items()}, cm


def table(cols):
    """cols: [(заголовок, row)] → таблица метрики × столбцы."""
    L = ["| метрика | " + " | ".join(h for h, _ in cols) + " |", "|---|" + "---|" * len(cols)]
    for name, fn in METRICS:
        L.append("| %s | %s |" % (name, " | ".join(fn(r) for _, r in cols)))
    return L


def top_tables(coins, side, eng):
    z = [(c, r) for (s, e, c), r in coins.items() if s == side and e == eng and r["n"] > 0]
    z.sort(key=lambda kv: (kv[1]["total"], kv[0]))
    hdr = ["| № | монета | сделок | прибыльных, % | итого, $ | итого, % счёта | средняя сделка, % позиции | макс. просадка, % | худшие сутки, % счёта | лучшие сутки, % счёта |", "|---|---|---|---|---|---|---|---|---|---|"]

    def line(i, c, r):
        return "| %d | %s | %d | %s | %s | %s | %s | %s | %s | %s |" % (
            i, c, r["n"], ms.pct(r.get("win"), 0), ms.f(r["total"]), ms.f(pc(r["total"], ACCT)), ms.f(pc(r.get("avg"), POS)), ms.f(r["ddp"] * 100),
            ms.f(pc(r["worst"], ACCT)), ms.f(pc(r["best"], ACCT)))
    worst = [line(i, c, r) for i, (c, r) in enumerate(z[:20], 1)]
    best = [line(i, c, r) for i, (c, r) in enumerate(reversed(z[-20:]), 1)]
    return hdr + worst, hdr + best, len(z)


def section():
    pdata, coins, diff, counts, _ = run()
    W = ["## 5. По месяцам, монетам и в процентах (счёт $2 500, позиция $500)", ""]
    W += ["Владелец (14.11): «нужно вот это сделать для каждой монеты, каждой стороны и каждого месяца и сделять это еще и в процентных соотношениях тк мне нужно знать просалку в процентах и приросты в процентах».", ""]
    W += ["**Допущение: счёт $2 500, позиция $500, без плеча** (В-89, `docs/plan/SETTLED.md`: «позиция $500 „полная“, депозит $2 500, без плеча»; В-90 — лимит числа позиций не вводится). Проценты — пересчёт $ на счёт $2 500; сам счёт в расчёте не моделируется (позиция всегда $500, без реинвестирования). Основа = правила бота; тики — задержка 200 мс на вход и выход (основной блок), минутки — второй блок.", "",
          "- **Месяцы:** сентябрь — только 15–30.09 (16 суток, **неполный**), октябрь — 31 сутки, ноябрь — только 1–13.11 (13 суток, **неполный**), «ALL» — всё окно (60 суток). Сделка относится к месяцу по дню входа.",
          "- **Проценты:** доходность и «$ в сутки», худшие/лучшие сутки, средние прибыльная/убыточная/сделка — в % от счёта $2 500 (каждая строка начинается со счёта $2 500, без реинвестирования); средние по сделке — ещё и в % от позиции $500 (= bps / 100). **Просадка % — от пика счёта** (2 500 + накопленный P&L на пике); просадка $ — от пика P&L строки (старт = 0).",
          "- **Ряды:** «итого», $/сутки, просадка, «без нового максимума», худшие/лучшие сутки — по суточному ряду (для монеты — по её суткам с P&L; для портфеля — по всем суткам); число сделок, средние, доля прибыльных, PF, доли выходов, время — по сделкам. Наибольшее расхождение «сумма сделок месяца − суточная сумма месяца»: портфель $%s, монета $%s (сделка в месяце — по дню входа, ноги могут исполниться в соседние сутки)." % (ms.f(diff[0]), ms.f(diff[1])),
          "- **Строки монет:** только те (монета, месяц), где у монеты есть сделки или P&L; без сделок в месяце строки нет. Для монет «худшие/лучшие сутки» — по суткам с P&L монеты; «без нового максимума» — от начала периода (монета без прибыльных суток остаётся «без максимума» весь период).",
          "- **Файлы** (UTF-8 с BOM, десятичная точка, колонки — англ. snake_case, `engine` = `tick` | `1m`, `month` = `2026-09p` | `2026-10` | `2026-11p` | `ALL`, `partial` = 1 у неполных): `docs/findings/p42-per-coin-month/long.csv` (%d строк), `short.csv` (%d), `portfolio.csv` (по стороне × движку × месяцу, `coin` = PORTFOLIO). Колонки: все метрики раздела 1 в $, затем `*_pct_acct` (от счёта), `max_drawdown_pct_peak`, `*_pct_pos` (от позиции)." % (counts["long"], counts["short"]), ""]
    W += ["### Портфель: сторона × месяц", ""]
    for side in ms.SIDES:
        for eng, _, en in ENG:
            W += ["#### %s, %s" % (ms.SIDE_RU[side], en), ""]
            W += table([(p[1] if p[0] != "ALL" else "**всё окно (60 сут.)**", pdata[(side, eng, p[0])]) for p in PERIODS]) + [""]
    W += ["### 20 худших и 20 лучших монет по итогу в % счёта (всё окно)", "",
          "Итого в % счёта = итого $ / $2 500; порядок тот же, что по $. Монеты со сделками в окне; полный список по монетам и месяцам — в CSV.", ""]
    for side in ms.SIDES:
        for eng, _, en in ENG:
            worst, best, n = top_tables(coins, side, eng)
            W += ["#### %s, %s: 20 худших из %d" % (ms.SIDE_RU[side], en, n), ""] + worst + [""]
            W += ["#### %s, %s: 20 лучших из %d" % (ms.SIDE_RU[side], en, n), ""] + best + [""]
    return W
