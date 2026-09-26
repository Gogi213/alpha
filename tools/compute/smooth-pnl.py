#!/usr/bin/env python3
"""Гладкость дневного PnL по готовым счётам (владелец 27.09: «есть варианты с более сглаженным pnl?»).

Без нового бэктеста: дневной PnL счёта $2500 / позиция $500 из готовых `portfolio-sim --json`:
    data/titration-dashboard/protection-dash-notrx-v16.json   главный, 3 соседа дашборда (без защит; потолок 3)
    data/titration-dashboard/protection-dash-v17-{money,filt}.json  П-02 вариантами (Г-105, Г-85, Г-86; фильтры Г-08 …)
    data/p05/protection-p05-{a1,a2,b,c}.json                  клетки П-05 (на проверке у Судьи)
    data/smooth/protection-e26-notrx.json                     окна BTC 1/2/3/4 ч (E26, тот же счёт без TRX; сентябрь без 14.09)

По месяцам отдельно (август 01–31, сентябрь 01–23; день без сделок = $0): итог, самый длинный отрезок без нового
максимума счёта (дней), доля прибыльных дней, дневной Шарп (среднее / σ по календарным дням, без годового множителя),
худший день, худшие 5 дней подряд. Смеси 2–3 вариантов — среднее их дневных PnL (каждый на 1/2 или 1/3 позиции,
чтобы деньги были сравнимы с одиночным), **без учёта занятости монеты** и общего потолка позиций.

    python tools/compute/smooth-pnl.py --x 25 --out data/smooth/smooth-2026-09-27.json
"""
import argparse
import datetime as dt
import itertools
import json
import os
import statistics as st

MONTHS = {"aug": ("август", "2026-08-01", "2026-08-31"), "sep": ("сентябрь", "2026-09-01", "2026-09-23")}


def days(a, b):
    d, e = dt.date.fromisoformat(a), dt.date.fromisoformat(b)
    out = []
    while d <= e:
        out.append(d.isoformat())
        d += dt.timedelta(days=1)
    return out


def row(grid, variant, epoch, max_pos=0, kill=0.0, exclude="нет"):
    for g in grid:
        if (g["variant"] == variant and g["epoch"] == epoch and g["max_pos"] == max_pos and g["kill"] == kill
                and g["exclude"] == exclude and g.get("day_stop", 0) == 0 and g.get("streak_stop", 0) == 0):
            return g
    return None


def metrics(daily, dlist):
    x = [daily.get(d, 0.0) for d in dlist]
    cum, peak, run, longest = 0.0, 0.0, 0, 0
    for v in x:
        cum += v
        if cum > peak + 1e-9:
            peak, run = cum, 0
        else:
            run += 1
            longest = max(longest, run)
    sd = st.pstdev(x)
    w5 = min(sum(x[i:i + 5]) for i in range(len(x) - 4))
    return {"usd": round(sum(x), 2), "no_high_days": longest, "win_days": round(sum(v > 0 for v in x) / len(x), 3),
            "trade_days": sum(v != 0 for v in x), "sharpe_d": round(st.mean(x) / sd, 3) if sd else None,
            "worst_day": round(min(x), 2), "worst5": round(w5, 2)}


def load_series():
    """имя → {"aug": daily, "sep": daily}, группа."""
    S = {}
    td = "data/titration-dashboard"
    dash = json.load(open(f"{td}/protection-dash-notrx-v16.json", encoding="utf-8"))
    for v in dash["variants"]:
        for mp, tag in ((0, ""), (3, " + потолок 3")):
            a, s = row(dash["grid"], v["name"], "август", mp), row(dash["grid"], v["name"], "сентябрь", mp)
            if a and s:
                S[v["name"] + tag] = ({"aug": a["daily"], "sep": s["daily"]}, "дашборд")
    for fn in ("protection-dash-v17-money.json", "protection-dash-v17-filt.json"):
        money = json.load(open(f"{td}/{fn}", encoding="utf-8"))
        for v in money["variants"]:
            a, s = row(money["grid"], v["name"], "август"), row(money["grid"], v["name"], "сентябрь")
            if a and s and v["name"] not in S:
                S[v["name"]] = ({"aug": a["daily"], "sep": s["daily"]}, "П-02")
    for part in ("a1", "a2", "b", "c"):
        p = json.load(open(f"data/p05/protection-p05-{part}.json", encoding="utf-8"))
        for v in p["variants"]:
            if v["name"] == "главный":
                continue
            a, s = row(p["grid"], v["name"], "август"), row(p["grid"], v["name"], "сентябрь")
            if a and s:
                S["П-05 " + v["name"][4:]] = ({"aug": a["daily"], "sep": s["daily"]}, "П-05 (у Судьи)")
    e26 = "data/smooth/protection-e26-notrx.json"
    if os.path.exists(e26):
        e = json.load(open(e26, encoding="utf-8"))
        for v in e["variants"]:
            for mp, tag in ((0, ""), (3, " + потолок 3")):
                a, s = row(e["grid"], v["name"], "август", mp), row(e["grid"], v["name"], "сентябрь", mp)
                if a and s:
                    S["E26 " + v["name"] + tag] = ({"aug": a["daily"], "sep": s["daily"]}, "E26 окна BTC")
    return S


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--x", type=float, default=25.0, help="допуск: не хуже главного больше чем на $X в каждом месяце")
    ap.add_argument("--main", default="BTC 4 ч: трейл 1/1")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    S = load_series()
    # дубли (один и тот же счёт под разными именами: «сверка», П-05 a45-u10k = главный) — оставить первое имя
    seen, uniq = {}, {}
    for n, v in S.items():
        sig = json.dumps(v[0], sort_keys=True)
        if sig in seen:
            continue
        seen[sig] = n
        uniq[n] = v
    S = uniq
    # дни месяца + дни закрытия переноса (сделка августа, закрытая 01.09, — в августе, как на дашборде, В-94)
    D = {m: sorted(set(days(f, t)) | {d for ser, _g in S.values() for d in ser[m]}) for m, (_n, f, t) in MONTHS.items()}
    res = {name: {"group": g, **{m: metrics(ser[m], D[m]) for m in MONTHS}} for name, (ser, g) in S.items()}
    # смеси: окна BTC E26 (все пары и тройки) + главный с каждым из лучших одиночных по деньгам
    mix_pool = [n for n in S if n.startswith("E26 ") and "потолок" not in n]
    base = [n for n in S if "потолок" not in n and not n.startswith("E26 ")]
    top = sorted(base, key=lambda n: -min(res[n]["aug"]["usd"], res[n]["sep"]["usd"]))[:8]
    combos = [c for k in (2, 3) for c in itertools.combinations(mix_pool, k)]
    combos += [(a.main, n) for n in top if n != a.main]
    combos += [c for c in itertools.combinations([n for n in top if n != a.main], 2)]
    for c in combos:
        name = "смесь: " + " + ".join(c)
        ser = {m: {d: sum(S[n][0][m].get(d, 0.0) for n in c) / len(c) for d in D[m]} for m in MONTHS}
        res[name] = {"group": "смесь (без занятости монеты)", **{m: metrics(ser[m], D[m]) for m in MONTHS}}
    main = res[a.main]
    ok = {n: r for n, r in res.items()
          if all(r[m]["usd"] >= main[m]["usd"] - a.x for m in MONTHS)}
    lead = sorted(ok, key=lambda n: (-min(ok[n]["aug"]["sharpe_d"] or -9, ok[n]["sep"]["sharpe_d"] or -9)))
    # глаже главного в обоих месяцах: Шарп выше И худшие 5 дней не хуже И отрезок без максимума не длиннее
    smoother = [n for n in ok if n != a.main and all(
        (ok[n][m]["sharpe_d"] or -9) > (main[m]["sharpe_d"] or -9) and ok[n][m]["worst5"] >= main[m]["worst5"]
        and ok[n][m]["no_high_days"] <= main[m]["no_high_days"] for m in MONTHS)]
    out = {"x_usd": a.x, "n_variants": len(S), "n_mixes": len(combos), "main": a.main, "results": res, "leaders": lead,
           "smoother_both_months": smoother}
    json.dump(out, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
    f = lambda r: (f"${r['usd']:+.0f} · без максимума {r['no_high_days']} дн · в плюс {r['win_days']*100:.0f} % дней · "
                   f"Шарп {r['sharpe_d']:+.2f} · худший день ${r['worst_day']:+.0f} · худшие 5 дн ${r['worst5']:+.0f}")
    print(f"вариантов {len(S)}, смесей {len(combos)}; не хуже главного больше чем на ${a.x:.0f} в каждом месяце: {len(ok)}; "
          f"глаже главного в обоих месяцах по трём мерам: {smoother}")
    for n in [a.main] + [x for x in lead if x != a.main][:15]:
        print(f"{n} [{res[n]['group']}]\n   авг: {f(res[n]['aug'])}\n   сен: {f(res[n]['sep'])}")


if __name__ == "__main__":
    main()
