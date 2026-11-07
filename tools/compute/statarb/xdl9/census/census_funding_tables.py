#!/usr/bin/env python3
"""TK-493, П-39, перепись T2: markdown-таблицы отчёта из csv выхода census_funding.py (stdlib). Использование: python census_funding_tables.py <каталог t2> <файл вывода>.
Только форматирование чисел из csv; ничего не пересчитывает, кроме суммирования строк по половинам."""
import csv
import os
import sys


def rd(d, name):
    return list(csv.DictReader(l for l in open(os.path.join(d, name), encoding="utf-8", newline="") if not l.startswith("#")))


def f(v, nd=1):
    if v in ("", None):
        return "-"
    try:
        x = float(v)
    except ValueError:
        return str(v)
    if x == int(x) and abs(x) < 1e12 and nd >= 0 and "." not in str(v) and "e" not in str(v).lower():
        return "{:,}".format(int(x)).replace(",", " ")
    return ("{:,.%df}" % nd).format(x).replace(",", " ")


def md(head, rows):
    out = ["| " + " | ".join(head) + " |", "|" + "|".join("---" for _ in head) + "|"]
    for r in rows:
        out.append("| " + " | ".join(str(c) for c in r) + " |")
    return "\n".join(out)


def counts_table(d, univ, est):
    C = rd(d, "t2_counts.csv")

    def g(h, t, k):
        for r in C:
            if r["universe"] == univ and r["estimator"] == est and r["half"] == h and r["tau"] == str(t):
                return r[k]
        return ""
    head = ["Δ_h ≥, bps/ч", "пара-часов ф / о", "серий ф / о", "серий ≥2 ч ф / о", "макс. длина, ч", "S медиана / q90 серии (окно), bps", "пара×сутки ф / о / всего",
            "актив×сутки ф / о / всего", "активов ф / о / всего", "актив-эпизодов (всего)"]
    rows = []
    for t in (10, 25, 50, 100, 200):
        rows.append([t, "%s / %s" % (f(g("form", t, "pair_hours_hit")), f(g("win", t, "pair_hours_hit"))), "%s / %s" % (f(g("form", t, "runs")), f(g("win", t, "runs"))),
                     "%s / %s" % (f(g("form", t, "runs_ge2h")), f(g("win", t, "runs_ge2h"))), f(max(int(g("form", t, "run_len_max") or 0), int(g("win", t, "run_len_max") or 0))),
                     "%s / %s" % (f(g("win", t, "S_med")), f(g("win", t, "S_q90"))),
                     "%s / %s / %s" % (f(g("form", t, "pair_day_events")), f(g("win", t, "pair_day_events")), f(g("all", t, "pair_day_events"))),
                     "%s / %s / %s" % (f(g("form", t, "asset_day_events")), f(g("win", t, "asset_day_events")), f(g("all", t, "asset_day_events"))),
                     "%s / %s / %s" % (f(g("form", t, "assets_with_event")), f(g("win", t, "assets_with_event")), f(g("all", t, "assets_with_event"))), f(g("all", t, "asset_episodes"))])
    return md(head, rows)


def universes_table(d):
    C = rd(d, "t2_counts.csv")

    def g(u, e, h, t, k):
        for r in C:
            if r["universe"] == u and r["estimator"] == e and r["half"] == h and r["tau"] == str(t):
                return r[k]
        return ""
    head = ["вселенная", "оценка", "пар", "τ", "пара×сутки ф / о / всего", "актив×сутки ф / о / всего", "активов ф / о / всего"]
    rows = []
    names = {"U1": "U1 крипта, c1d2=1 (как C2)", "U2": "U2 крипта, шире (вердикт не «разное»)", "U3": "U3 все классы, c1d2=1", "G": "Gate × 8 бирж, крипта (описательно)"}
    for u in ("U1", "U2", "U3", "G"):
        for e in ("ante", "post"):
            for t in (25, 100):
                rows.append([names[u], e, f(g(u, e, "all", t, "pairs")), t, "%s / %s / %s" % (f(g(u, e, "form", t, "pair_day_events")), f(g(u, e, "win", t, "pair_day_events")), f(g(u, e, "all", t, "pair_day_events"))),
                             "%s / %s / %s" % (f(g(u, e, "form", t, "asset_day_events")), f(g(u, e, "win", t, "asset_day_events")), f(g(u, e, "all", t, "asset_day_events"))),
                             "%s / %s / %s" % (f(g(u, e, "form", t, "assets_with_event")), f(g(u, e, "win", t, "assets_with_event")), f(g(u, e, "all", t, "assets_with_event")))])
    return md(head, rows)


def legs_table(d):
    L = rd(d, "t2_legs.csv")
    head = ["биржа", "контрактов", "выплат", "интервал 1 / 4 / 8 ч, доля", "разрыв ≠ interval_h", "модуль r_h: p50 / p99 / p99,9 / max, bps/ч", "выплат с модулем r_h ≥ 25 / 100 / 200"]
    rows = []
    for r in L:
        rows.append([r["exchange"], f(r["contracts"]), f(r["payments"]), "%s / %s / %s" % (f(float(r["share_int_1h"]), 3), f(float(r["share_int_4h"]), 3), f(float(r["share_int_8h"]), 3)),
                     f(r["gap_ne_interval"]), "%s / %s / %s / %s" % (f(r["absrh_p50"], 2), f(r["absrh_p99"], 2), f(r["absrh_p999"], 1), f(r["absrh_max"], 0)),
                     "%s / %s / %s" % (f(r["pay_ge25_bps_per_h"]), f(r["pay_ge100_bps_per_h"]), f(r["pay_ge200_bps_per_h"]))])
    return md(head, rows)


def top_table(d, name, n=20):
    T = rd(d, name)
    head = ["№", "актив", "биржи A / B", "начало (UTC)", "ч", "Δ_h макс", "S, bps", "r_A / r_B в начале", "шорт (ставка выше) / ведущая нога", "оборот тонкой ноги в самый тихий час, USD", "сдвиг цены спреда макс., bps"]
    rows = []
    for r in T[:n]:
        rows.append([r["rank"], r["key"], "%s / %s" % (r["exA"], r["exB"]), r["start"], r["len"], f(r["delta_max"]), f(r["S"]), "%s / %s" % (f(r["rA_start"]), f(r["rB_start"])),
                     "%s / %s" % (r["short_leg"], r["lead_leg"]), f(r["turn_min_hour_min_usd"], 0), f(r["ds_max"], 0)])
    return md(head, rows)


def assetday_table(d):
    T = rd(d, "t2_events_tau100_assetday.csv")
    head = ["актив", "начало (UTC)", "пар в событии", "ч", "Δ_h макс", "лучшая пара A / B", "шорт / ведущая", "S, bps", "оборот тонкой ноги, самый тихий час, USD", "то же за все часы серии, USD",
            "3 тыс. $ / тихий час", "80 тыс. $ / тихий час", "сдвиг цены спреда макс., bps"]
    rows = []
    for r in T:
        rows.append([r["key"], r["date"], r["pairs_in_event"], r["hours"], f(r["delta_max"]), "%s / %s" % (r["exA"], r["exB"]), "%s / %s" % (r["short_leg"], r["lead_leg"]), f(r["S"]),
                     f(r["turn_min_hour_min_usd"], 0), f(r["turn_min_hours_usd"], 0), f(r["share3k"], 3), f(r["share80k"], 3), f(r["ds_max"], 0)])
    return md(head, rows)


def lead_table(d):
    L = rd(d, "t2_lead_exchange.csv")
    head = ["оценка", "τ", "биржа (ведущая нога)", "актив-часов ф / о", "активов ф / о"]
    rows = []
    for e in ("ante", "post"):
        for t in (25, 50, 100):
            exs = sorted({r["exchange"] for r in L if r["estimator"] == e and r["tau"] == str(t)})
            for x in exs:
                def g(h, k):
                    for r in L:
                        if r["estimator"] == e and r["tau"] == str(t) and r["half"] == h and r["exchange"] == x:
                            return r[k]
                    return "0"
                rows.append([e, t, x, "%s / %s" % (g("form", "asset_hours"), g("win", "asset_hours")), "%s / %s" % (g("form", "assets"), g("win", "assets"))])
    return md(head, rows)


def loo_table(d):
    L = rd(d, "t2_leave_one_exchange_ante.csv")
    head = ["исключена биржа", "пар", "τ=25 пара×сутки ф / о / всего", "τ=25 актив×сутки всего", "τ=25 активов ф / о / всего", "τ=100 пара×сутки", "τ=100 актив×сутки", "τ=100 активов"]
    rows = []
    for x in ["-"] + ["binance", "bybit", "okx", "bingx", "bitget", "kraken", "aster", "lighter"]:
        def g(t, h, k):
            for r in L:
                if r["excluded"] == x and r["tau"] == str(t) and r["half"] == h:
                    return r[k]
            return ""
        rows.append([x if x != "-" else "ничего", f(g(25, "all", "pairs")), "%s / %s / %s" % (g(25, "form", "pair_day_events"), g(25, "win", "pair_day_events"), g(25, "all", "pair_day_events")),
                     g(25, "all", "asset_day_events"), "%s / %s / %s" % (g(25, "form", "assets"), g(25, "win", "assets"), g(25, "all", "assets")), g(100, "all", "pair_day_events"),
                     g(100, "all", "asset_day_events"), g(100, "all", "assets")])
    return md(head, rows)


def daily_table(d, n=10):
    D = rd(d, "t2_daily_events.csv")
    d25 = [r for r in D if r["tau"] == "25"]
    d100 = {r["day"]: r for r in D if r["tau"] == "100"}
    tot = sum(int(r["pair_day_events"]) for r in d25)
    srt = sorted(d25, key=lambda r: -int(r["pair_day_events"]))[:n]
    head = ["сутки", "половина", "пара×сутки (τ=25)", "доля от 644", "актив×сутки", "Δ_h макс за сутки", "пара×сутки (τ=100)"]
    rows = [[r["date"], r["half"], r["pair_day_events"], f(int(r["pair_day_events"]) / tot * 100, 1) + " %", r["asset_day_events"], f(r["delta_max"]), d100[r["day"]]["pair_day_events"]] for r in srt]
    return md(head, rows), tot


def persist_table(d):
    P = rd(d, "t2_persistence.csv")
    head = ["τ", "половина", "ч с сигналом", "начислено в том же направлении, среднее за час, bps", "медиана за час", "доля часов > 0", "серий", "серий с R > 0", "серий с R > 12,6", "ΣS (известная), bps", "ΣR (начисленная), bps"]
    rows = []
    for r in P:
        if r["hit_hours"] == "0":
            continue
        rows.append([r["tau"], r["half"], f(r["hit_hours"]), f(r["real_mean_per_hour"]), f(r["real_median_per_hour"]), f(float(r["share_hours_real_gt0"]) * 100, 0) + " %", f(r["runs"]),
                     f(float(r["share_runs_R_gt0"]) * 100, 0) + " %", "%s (%s %%)" % (r["R_gt_fee_runs"], f(float(r["share_runs_R_gt_fee"]) * 100, 0)), f(r["S_ante_sum"], 0), f(r["R_post_sum"], 0)])
    return md(head, rows)


def birb_tables(d):
    P = rd(d, "t2_birb_payments.csv")
    head = ["биржа", "время UTC", "ставка, %", "interval, ч", "r_h, bps/ч"]
    rows = []
    for r in P:
        if r["time_utc"].startswith("23.10") and r["exchange"] in ("binance", "bybit"):
            rows.append([r["exchange"], r["time_utc"][6:], f(r["rate_pct"], 4), r["interval_h"], f(r["r_h_bps"], 1)])
    t1 = md(head, rows)
    B = rd(d, "t2_birb_pairs.csv")
    head2 = ["оценка", "пара", "Δ_h макс (23–24.10)", "когда", "ч ≥25", "ч ≥50", "ч ≥100", "ΣΔ за 48 ч, bps"]
    rows2 = [[r["estimator"], r["pair"], f(r["delta_max_bps_h"]), r["at"], r["hours_ge25"], r["hours_ge50"], r["hours_ge100"], f(r["sum_delta_bps"], 0)] for r in B]
    return t1, md(head2, rows2)


def seller_tables(d):
    L = rd(d, "t2_seller_legs.csv")
    head = ["актив", "биржа", "половина", "выплат", "interval, ч", "r_h среднее", "r_h мин / макс", "модуль r_h макс (когда)"]
    rows = []
    for r in L:
        if r["key"] == "BIRB":
            continue
        rows.append([r["key"], r["exchange"], r["half"], r["payments"], r["interval_h"], f(r["r_h_mean"], 3), "%s / %s" % (f(r["r_h_min"], 2), f(r["r_h_max"], 2)), "%s (%s)" % (f(r["abs_max"], 2), r["abs_max_at"])])
    t1 = md(head, rows)
    S = rd(d, "t2_seller_pairs.csv")
    best = {}
    for r in S:
        k = (r["key"], r["half"])
        if k not in best or float(r["delta_max"]) > float(best[k]["delta_max"]):
            best[k] = r
    head2 = ["актив", "половина", "пар (вселенная U2)", "макс Δ_h по парам, bps/ч", "пара", "когда", "пар-часов Δ_h ≥ 10"]
    rows2 = []
    for key in ("WCT", "H", "LAB", "BIRB"):
        for h in ("form", "win"):
            lst = [r for r in S if r["key"] == key and r["half"] == h]
            if not lst:
                continue
            b = best[(key, h)]
            rows2.append([key, h, len(lst), f(b["delta_max"], 2), b["pair"], b["at"], sum(int(r["h_ge10"]) for r in lst)])
    return t1, md(head2, rows2)


def class_table(d):
    L = rd(d, "t2_by_class.csv")
    head = ["класс (U3)", "пар", "τ=25 пара×сутки ф / о", "τ=25 активов ф / о", "τ=100 пара×сутки ф / о"]
    rows = []
    for k in sorted({r["klass"] for r in L}):
        def g(t, h, f_):
            for r in L:
                if r["estimator"] == "ante" and r["klass"] == k and r["tau"] == str(t) and r["half"] == h:
                    return r[f_]
        rows.append([k, g(25, "form", "pairs"), "%s / %s" % (g(25, "form", "pair_day_events"), g(25, "win", "pair_day_events")), "%s / %s" % (g(25, "form", "assets"), g(25, "win", "assets")),
                     "%s / %s" % (g(100, "form", "pair_day_events"), g(100, "win", "pair_day_events"))])
    return md(head, rows)


def expair_table(d, n=12):
    L = rd(d, "t2_by_exchange_pair.csv")
    L = [r for r in L if r["estimator"] == "ante" and r["tau"] == "25"]
    pairs = sorted({r["exchange_pair"] for r in L})
    rows = []
    for p in pairs:
        def g(h, k):
            for r in L:
                if r["exchange_pair"] == p and r["half"] == h:
                    return int(r[k])
        rows.append((g("win", "pair_day_events") + g("form", "pair_day_events"), p, g("form", "pairs"), g("form", "pair_day_events"), g("win", "pair_day_events"), g("form", "assets"), g("win", "assets")))
    rows.sort(reverse=True)
    head = ["пара бирж", "пар (активов)", "пара×сутки ф / о (τ=25)", "активов ф / о"]
    return md(head, [[r[1], r[2], "%s / %s" % (r[3], r[4]), "%s / %s" % (r[5], r[6])] for r in rows[:n]])


def main():
    d, out = sys.argv[1], sys.argv[2]
    parts = {}
    parts["A_ante"] = counts_table(d, "U1", "ante")
    parts["A_post"] = counts_table(d, "U1", "post")
    parts["UNIV"] = universes_table(d)
    parts["LEGS"] = legs_table(d)
    parts["TOP_ante"] = top_table(d, "t2_top20_ante_tau25.csv")
    parts["TOP_post"] = top_table(d, "t2_top20_post_tau25.csv")
    parts["ASSETDAY"] = assetday_table(d)
    parts["LEAD"] = lead_table(d)
    parts["LOO"] = loo_table(d)
    parts["DAILY"], tot = daily_table(d)
    parts["PERSIST"] = persist_table(d)
    parts["BIRB_PAY"], parts["BIRB_PAIRS"] = birb_tables(d)
    parts["SELLER_LEGS"], parts["SELLER_PAIRS"] = seller_tables(d)
    parts["CLASS"] = class_table(d)
    parts["EXPAIR"] = expair_table(d)
    with open(out, "w", encoding="utf-8", newline="\n") as fh:
        for k, v in parts.items():
            fh.write("@@%s\n%s\n" % (k, v))
    print("таблиц", len(parts), "суточных событий tau=25 всего", tot)


if __name__ == "__main__":
    main()
