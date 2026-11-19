"""TK-618 шаг 1: вскрытие сделок базы — таблицы сумма $ / среднее на сделку / число по месяцам и срезам; MFE/MAE и отданная прибыль. Описательно: 0 испытаний.
  python3 short_autopsy_report.py <base-trades.csv> <path.csv> <выход.md>
Метрика — gross − fees (без фандинга), $ на позицию $500; медиан нет. Границы срезов по NATR/BTC — терцили/квартили самой выборки (назначены здесь до чтения таблиц)."""
import sys

import numpy as np
import pandas as pd

MON = ["jul", "aug", "sep", "oct", "nov"]
NOTIONAL = 500.0


def tab(df, by, title, out, bins=None, labels=None):
    g = df.copy()
    if bins is not None:
        g["_k"] = pd.cut(g[by], bins, labels=labels, include_lowest=True)
        key = "_k"
    else:
        key = by
    out.append("\n### %s\n" % title)
    out.append("| срез | сделок | Σ $ | $/сделку | " + " | ".join("%s Σ / n" % m for m in MON) + " |")
    out.append("|---|---:|---:|---:|" + "---|" * len(MON))
    for k, x in g.groupby(key, observed=True):
        cells = []
        for m in MON:
            y = x[x.month == m]
            cells.append("%s / %d" % ("%+.1f" % y.pnl.sum() if len(y) else "—", len(y)))
        out.append("| %s | %d | %+.2f | %+.3f | %s |" % (k, len(x), x.pnl.sum(), x.pnl.mean(), " | ".join(cells)))


def main(base, path, outp):
    b = pd.read_csv(base)
    p = pd.read_csv(path)
    d = b.merge(p.drop(columns=["exit_ms"]), on=["sym", "entry_ms"], how="left")
    assert len(d) == len(b), (len(d), len(b))
    d["real_bp"] = d.pnl / NOTIONAL * 1e4
    d["give_bp"] = d.mfe_bp - d.real_bp
    d["hour4"] = ((d.hour_utc + 4) % 24) // 4 * 4
    d["wday"] = pd.to_datetime(d.date).dt.dayofweek
    tot = d.pnl.sum()
    out = ["# TK-618 шаг 1: вскрытие базы шорта (431 сделка, 01.07–13.11), описательно\n",
           "Σ(gross − fees) %.2f $, сделок %d, $/сделку %+.3f; без пути (нет ленты): %d.\n" % (tot, len(d), d.pnl.mean(), int(d.mfe_bp.isna().sum()))]
    tab(d, "month", "Месяц", out)
    tab(d, "kind", "Вид выхода", out)
    d["tk"] = pd.cut(d.takes, [-1, 0, 3, 7, 15, 16], labels=["0", "1–3", "4–7", "8–15", "16"])
    tab(d, "tk", "Число исполненных тейков", out)
    tab(d, "hold_min", "Удержание, мин", out, [0, 1, 5, 15, 30, 60, 120, 240, 1e9], ["≤1", "1–5", "5–15", "15–30", "30–60", "60–120", "120–240", ">240"])
    tab(d, "hour4", "Час входа GMT+4 (блок 4 ч, начало)", out)
    tab(d, "wday", "День недели входа (0 = пн)", out)
    for col, nm in (("natr30_bp", "NATR30, бп"), ("natr60_bp", "NATR60, бп")):
        q = d[col].quantile([1 / 3, 2 / 3]).to_list()
        tab(d, col, "%s (терцили: %.1f / %.1f)" % (nm, q[0], q[1]), out, [-1] + q + [1e9], ["низ", "середина", "верх"])
    for col, nm in (("btc_24h_bp", "BTC за 24 ч до сигнала, бп"), ("btc_4h_bp", "BTC за 4 ч до сигнала, бп"), ("btc_1h_bp", "BTC за 1 ч до сигнала, бп")):
        q = d[col].quantile([0.25, 0.5, 0.75]).to_list()
        tab(d, col, "%s (квартили: %s)" % (nm, " / ".join("%.0f" % v for v in q)), out, [-1e9] + q + [1e9], ["q1 (низ)", "q2", "q3", "q4 (верх)"])
    c = d.groupby("sym").pnl.agg(["sum", "mean", "count"]).sort_values("sum")
    out.append("\n### Монеты\n")
    out.append("Монет с ≥1 сделкой: %d. Топ-5 по Σ: %+.1f $ (%.0f %% итога), топ-10: %+.1f, худшие 5: %+.1f, худшие 10: %+.1f. Монет с ≥3 сделками: %d (Σ %+.1f $), с 1 сделкой: %d (Σ %+.1f $)." % (
        len(c), c["sum"].tail(5).sum(), 100 * c["sum"].tail(5).sum() / tot, c["sum"].tail(10).sum(), c["sum"].head(5).sum(), c["sum"].head(10).sum(),
        (c["count"] >= 3).sum(), c[c["count"] >= 3]["sum"].sum(), (c["count"] == 1).sum(), c[c["count"] == 1]["sum"].sum()))
    out.append("\nХудшие 8: " + "; ".join("%s %+.1f (%d)" % (i, r["sum"], r["count"]) for i, r in c.head(8).iterrows()))
    out.append("\nЛучшие 8: " + "; ".join("%s %+.1f (%d)" % (i, r["sum"], r["count"]) for i, r in c.tail(8).iloc[::-1].iterrows()))
    ds = d.groupby("date").pnl.sum().sort_values()
    out.append("\n### Сутки\n")
    out.append("Суток со сделками: %d. Топ-5 суток: %+.1f $ (%.0f %% итога), худшие 5: %+.1f $. Лучшие: %s. Худшие: %s." % (
        len(ds), ds.tail(5).sum(), 100 * ds.tail(5).sum() / tot, ds.head(5).sum(),
        "; ".join("%s %+.1f" % (i, v) for i, v in ds.tail(5).iloc[::-1].items()), "; ".join("%s %+.1f" % (i, v) for i, v in ds.head(5).items())))
    out.append("\n### MFE / MAE (бп от цены входа; до выхода; шорт — вниз выгодно)\n")
    out.append("| группа | сделок | MFE среднее | MAE среднее | реализовано (бп) | отдано = MFE − реализовано | доля MFE ≥ 100 / 200 / 300 / 500 бп, % |")
    out.append("|---|---:|---:|---:|---:|---:|---|")
    groups = [("все", d)] + [(k, g) for k, g in d.groupby("kind")] + [("убыточные", d[d.pnl < 0]), ("прибыльные", d[d.pnl > 0])] + [("месяц " + m, d[d.month == m]) for m in MON]
    for name, x in groups:
        sh = " / ".join("%.0f" % (100 * (x.mfe_bp >= v).mean()) for v in (100, 200, 300, 500))
        out.append("| %s | %d | %.0f | %.0f | %.0f | %.0f | %s |" % (name, len(x), x.mfe_bp.mean(), x.mae_bp.mean(), x.real_bp.mean(), x.give_bp.mean(), sh))
    lo = d[d.pnl < 0]
    out.append("\nОтдано по трейлу (Σ MFE − реализовано, в $ при $500): %.0f $. Убыточные (%d): Σ MFE до выхода %.0f $; с MFE ≥ 100 бп: %d (Σ реализованного %+.1f $)." % (
        d[d.kind == "trail"].give_bp.sum() * NOTIONAL / 1e4, len(lo), lo.mfe_bp.sum() * NOTIONAL / 1e4, int((lo.mfe_bp >= 100).sum()), lo[lo.mfe_bp >= 100].pnl.sum()))
    q = d.mfe_bp.quantile([.1, .25, .5, .75, .9]).round(0).to_list()
    out.append("\nКвантили MFE, бп (10/25/50/75/90 %%): %s. Время до MFE, мин: среднее %.1f; у трейла %.1f; у rt %.1f; у стопа %.1f." % (
        q, d.t_mfe_min.mean(), d[d.kind == "trail"].t_mfe_min.mean(), d[d.kind == "rt"].t_mfe_min.mean(), d[d.kind == "stop"].t_mfe_min.mean()))
    out.append("\n### Ход цены после входа в пользу стороны (бп; среднее по сделкам, у которых лента дошла до горизонта)\n")
    out.append("| горизонт | сделок | средний ход | доля > 0, % | средний MFE | средний MAE |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for h in (1, 5, 15, 30, 60, 120, 240):
        x = d[d["r%d_bp" % h].notna()]
        out.append("| %d мин | %d | %+.1f | %.0f | %.0f | %.0f |" % (h, len(x), x["r%d_bp" % h].mean(), 100 * (x["r%d_bp" % h] > 0).mean(), x["mfe%d_bp" % h].mean(), x["mae%d_bp" % h].mean()))
    out.append("\n### Месяцы рядом (средние признаков сделок)\n")
    out.append("| признак | июль | август | сентябрь | октябрь | 01–13.11 |")
    out.append("|---|---:|---:|---:|---:|---:|")
    for col in ("natr30_bp", "natr60_bp", "btc_24h_bp", "btc_4h_bp", "mfe_bp", "mae_bp", "hold_min", "takes", "real_bp"):
        out.append("| %s | %s |" % (col, " | ".join("%.1f" % d[d.month == m][col].mean() for m in MON)))
    out.append("| сделок на сутки | %s |" % " | ".join("%.2f" % (len(d[d.month == m]) / n) for m, n in zip(MON, (31, 31, 30, 31, 13))))
    for kk, nm in (("rt", "доля rt-выходов, %"), ("stop", "доля стопов, %"), ("trail", "доля трейла, %")):
        out.append("| %s | %s |" % (nm, " | ".join("%.1f" % (100 * (d[d.month == m].kind == kk).mean()) for m in MON)))
    open(outp, "w", encoding="utf-8").write("\n".join(out) + "\n")
    d.to_csv(outp.replace(".md", "-joined.csv"), index=False)
    print("written", outp)


if __name__ == "__main__":
    main(*sys.argv[1:4])
