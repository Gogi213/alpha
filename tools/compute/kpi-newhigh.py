#!/usr/bin/env python3
"""KPI владельца «дней до перехая» (В-120, 27.09) и рейтинг ровности роста по готовым счётам.

Определение (фиксируется здесь и в отчёте). Кривая счёта — по закрытиям сделок (`portfolio-sim --closes-out`,
реализованный PnL, $2500 / позиция $500), каждый период отдельно с нуля: август (с закрытиями 01.09, В-94),
сентябрь 01–23, «август + сентябрь» одним счётом. Новый максимум — закрытие, после которого счёт строго выше всех
прежних значений (старт периода = максимум 0). Период «до перехая» — время от одного максимума до следующего, в часах;
собираются ВСЕ такие периоды (владелец: «надо собирать не один а все перехаи»). Незакрытый хвост — от последнего
максимума до конца периода — отдельно, в распределение не входит. Дневной вариант — то же по дневной кривой, в днях.

Остальные колонки: R² линейной регрессии дневной кривой капитала на номер дня (прямолинейность), доля плюсовых недель
(7-дневные блоки от начала месяца), просадка по закрытиям / прибыль, худший день, сделок, «эпизоды» — группы сделок,
между закрытиями которых ≥ 4 ч (приближение эпизодов просадки BTC: удержание ≤ 4 ч).

Рейтинг: 1) плюс в обоих месяцах и сделок ≥ 30 в августе / ≥ 10 в сентябре; 2) p90 периода до перехая, взвешенный
временем (длина периода, в котором проходит 90 % времени месяца; хвост входит), по худшему месяцу; счётный p90
вырождается у вариантов с тысячами сделок (много нулевых периодов); затем худший период с хвостом; 3) остальное справочно. Смеси «среднее» — дневные и
закрытия двух вариантов на половине позиции, без занятости монеты; смеси «одним счётом» — точный прогон
(`набор1+набор2`, занятость монеты учтена).

    python tools/compute/kpi-newhigh.py --in-dir data/kpi --out data/kpi/kpi-2026-09-27.json
"""
import argparse
import datetime as dt
import glob
import itertools
import json
import math
import os
import statistics as st

PERIODS = {"aug": ("август", "2026-08-01", "2026-09-01"), "sep": ("сентябрь", "2026-09-01", "2026-09-24"),
           "augsep": ("август+сентябрь", "2026-08-01", "2026-09-24")}
MS_H = 3_600_000


def ms(day):
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def q(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    f, c = math.floor(k), math.ceil(k)
    return xs[f] + (xs[c] - xs[f]) * (k - f)


def periods_to_high(events, start, end):
    """events: [(t, pnl)] по времени; t и start/end в одних единицах. → (все закрытые периоды, хвост, число максимумов)."""
    cum, peak, t_peak, out, n_high = 0.0, 0.0, start, [], 0
    for t, p in events:
        cum += p
        if cum > peak + 1e-9:
            out.append(t - t_peak)
            peak, t_peak, n_high = cum, t, n_high + 1
    return out, max(end, events[-1][0] if events else end) - t_peak, n_high


def tw_q(xs, p):
    """Квантиль, взвешенный временем: длина периода, в котором лежит доля p всего времени (xs — все периоды + хвост)."""
    xs = sorted(x for x in xs if x > 0)
    tot, c = sum(xs), 0.0
    for x in xs:
        c += x
        if c >= p * tot - 1e-9:
            return x
    return None


def dist(xs, tail):
    return {"n": len(xs), "median": q(xs, 0.5), "p75": q(xs, 0.75), "p90": q(xs, 0.9), "max": max(xs) if xs else None,
            "tail": tail, "worst": max([tail] + xs), "tw_median": tw_q(xs + [tail], 0.5), "tw_p90": tw_q(xs + [tail], 0.9)}


def month_metrics(closes, pk):
    _n, a, b = PERIODS[pk]
    s, e = ms(a), ms(b)
    ev = sorted((t, p) for t, p in closes)
    hrs, tail_h, _nh = periods_to_high([(t / MS_H, p) for t, p in ev], s / MS_H, e / MS_H)
    daily = {}
    for t, p in ev:
        d = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        daily[d] = daily.get(d, 0.0) + p
    days = []
    d = dt.date.fromisoformat(a)
    last = max([dt.date.fromisoformat(b) - dt.timedelta(days=1)] + [dt.date.fromisoformat(x) for x in daily])
    while d <= last:
        days.append(d.isoformat())
        d += dt.timedelta(days=1)
    x = [daily.get(dd, 0.0) for dd in days]
    dys, tail_d, _ = periods_to_high(list(enumerate(x)), -1, len(x) - 1)
    cum = list(itertools.accumulate(x))
    n = len(cum)
    mx, my = (n - 1) / 2, st.mean(cum)
    sxx = sum((i - mx) ** 2 for i in range(n))
    sxy = sum((i - mx) * (c - my) for i, c in enumerate(cum))
    syy = sum((c - my) ** 2 for c in cum)
    r2 = (sxy * sxy / (sxx * syy)) if sxx and syy else None
    weeks = [sum(x[i:i + 7]) for i in range(0, n, 7)]
    peak = c = dd = 0.0
    for _t, p in ev:
        c += p
        peak = max(peak, c)
        dd = max(dd, peak - c)
    total = sum(p for _t, p in ev)
    eps, last_t = 0, None
    for t, _p in ev:
        if last_t is None or t - last_t >= 4 * MS_H:
            eps += 1
        last_t = t
    return {"usd": round(total, 2), "n": len(ev), "episodes": eps, "hours": dist(hrs, tail_h), "days": dist(dys, tail_d),
            "r2": round(r2, 3) if r2 is not None else None, "slope_sign": 1 if sxy > 0 else -1,
            "plus_weeks": round(sum(w > 0 for w in weeks) / len(weeks), 2), "dd_over_profit": round(dd / total, 2) if total > 0 else None,
            "dd_usd": round(dd, 2), "worst_day": round(min(x), 2) if x else None}


def rolling_kpi(closes):
    """KPI, устойчивый к старту (CEO 27.09, В-120): непрерывный счёт «август + сентябрь» (без обнуления 01.09), сетка t —
    каждый час. «от максимума» (основной): время от t до первого закрытия, после которого счёт строго выше максимума,
    достигнутого к t; «со старта»: до первого закрытия выше значения счёта в t (как будто бот запущен в t). Незакрытые к
    концу данных (24.09 00:00) — цензура: считается время до конца, доля цензуры печатается. По месяцам — по t в месяце."""
    ev = sorted(closes)
    s0, e0 = ms("2026-08-01"), ms("2026-09-24")
    times = [t for t, _ in ev]
    eq, c = [], 0.0
    for _t, p in ev:
        c += p
        eq.append(c)
    # следующий строгий рекорд после индекса: считаем по сетке часов двумя указателями
    import bisect
    out = {"aug": {"max": [], "start": [], "cens_max": 0, "cens_start": 0}, "sep": {"max": [], "start": [], "cens_max": 0, "cens_start": 0}}
    # префиксный максимум
    pm, m = [], 0.0
    for v in eq:
        m = max(m, v)
        pm.append(m)
    # для «со старта»: для каждого уровня ищем первое закрытие выше — линейный поиск вперёд с кешем по часам
    t = s0
    while t < e0:
        pk = "aug" if t < ms("2026-09-01") else "sep"
        i = bisect.bisect_right(times, t)  # закрытия строго после t
        cur = eq[i - 1] if i > 0 else 0.0
        mx = pm[i - 1] if i > 0 else 0.0
        mx = max(mx, 0.0)
        r_max = r_st = None
        for j in range(i, len(ev)):
            if r_st is None and eq[j] > cur + 1e-9:
                r_st = (times[j] - t) / MS_H
            if eq[j] > mx + 1e-9:
                r_max = (times[j] - t) / MS_H
                break
        if r_st is None:
            for j in range(i, len(ev)):
                if eq[j] > cur + 1e-9:
                    r_st = (times[j] - t) / MS_H
                    break
        o = out[pk]
        if r_max is None:
            o["cens_max"] += 1
            r_max = (e0 - t) / MS_H
        if r_st is None:
            o["cens_start"] += 1
            r_st = (e0 - t) / MS_H
        o["max"].append(r_max)
        o["start"].append(r_st)
        t += MS_H
    res = {}
    for pk, o in out.items():
        n = len(o["max"])
        res[pk] = {k: {"median": q(o[k], 0.5), "p90": q(o[k], 0.9), "max": max(o[k]), "cens": round(o["cens_" + k] / n, 3)}
                   for k in ("max", "start")}
    return res


def load(in_dir):
    """имя → (группа, {pk: closes}) для потолка 0; «+ потолок 3» — отдельными строками."""
    group = {"dash": "дашборд", "money": "П-02", "filt": "П-02 фильтр", "e26": "окна BTC (E26)"}
    S = {}
    for f in sorted(glob.glob(os.path.join(in_dir, "*-closes.json"))):
        tag = os.path.basename(f)[:-len("-closes.json")]
        g = "П-05 (у Судьи)" if tag.startswith("p05") else group.get(tag, tag)
        for v, eps in json.load(open(f, encoding="utf-8")).items():
            for mp in ("0", "3"):
                ser = {pk: eps.get(PERIODS[pk][0], {}).get(mp) for pk in PERIODS}
                if any(s is None for s in ser.values()):
                    continue
                name = (v[4:] if v.startswith("p05-") else v) + ("" if mp == "0" else " + потолок 3")
                if tag.startswith("p05") and v == "главный":
                    continue
                if tag.startswith("p05"):
                    name = "П-05 " + name
                S.setdefault(name, (g, ser))
    return S


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--in-dir", default="data/kpi")
    ap.add_argument("--main", default="BTC 4 ч: трейл 1/1")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    S = load(a.in_dir)
    # дубли одного счёта под разными именами
    seen, uniq = set(), {}
    for n, v in S.items():
        sig = json.dumps(v[1]["aug"][:50]) + json.dumps(v[1]["sep"][:50]) + str(len(v[1]["aug"]))
        if sig not in seen:
            seen.add(sig)
            uniq[n] = v
    S = uniq
    res = {n: {"group": g, **{pk: month_metrics(ser[pk], pk) for pk in PERIODS}, "roll": rolling_kpi(ser["augsep"])}
           for n, (g, ser) in S.items()}
    # смеси «среднее» (без занятости): пары из окон BTC и из 8 лучших по худшему месяцу $, плюс главный с каждым
    single = [n for n in S if "потолок" not in n and "одним счётом" not in n]
    top = sorted(single, key=lambda n: -min(res[n]["aug"]["usd"], res[n]["sep"]["usd"]))[:8]
    e26 = [n for n in single if n.startswith("E26 ")]
    pairs = {tuple(sorted(c)) for c in list(itertools.combinations(e26, 2)) + list(itertools.combinations(top, 2))
             + [(a.main, n) for n in top if n != a.main]}
    for c in sorted(pairs):
        ser = {pk: [(t, p / len(c)) for n in c for t, p in S[n][1][pk]] for pk in PERIODS}
        res["смесь: " + " + ".join(c)] = {"group": "смесь среднее (без занятости)", **{pk: month_metrics(ser[pk], pk) for pk in PERIODS},
                                         "roll": rolling_kpi(ser["augsep"])}
    ok = [n for n, r in res.items() if r["aug"]["usd"] > 0 and r["sep"]["usd"] > 0 and r["aug"]["n"] >= 30 and r["sep"]["n"] >= 10]
    # счётный p90 вырождается (тысячи сделок → много нулевых периодов на соседних закрытиях при хвостах 200+ ч),
    # поэтому порядок — по p90, взвешенному временем (хвост входит), по худшему месяцу; затем худший период с хвостом
    # с 27.09 (CEO): порядок — по p90 «от максимума» со скользящим стартом (непрерывный счёт), худший месяц; затем максимум
    key = lambda n: (max(res[n]["roll"]["aug"]["max"]["p90"], res[n]["roll"]["sep"]["max"]["p90"]),
                     max(res[n]["roll"]["aug"]["max"]["max"], res[n]["roll"]["sep"]["max"]["max"]))
    rank = sorted(ok, key=key)
    json.dump({"definition": __doc__.split("\n\n")[1], "main": a.main, "n_rows": len(res), "n_pairs": len(pairs),
               "rank": rank, "results": res}, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
    h = lambda x: "—" if x is None else f"{x:.0f}"
    print(f"рядов {len(res)} (смесей-среднее {len(pairs)}); плюс в обоих месяцах и сделок достаточно: {len(ok)}")
    print("место | вариант | скользящий старт от максимума, дн [авг ; сен]: медиана / p90 / макс (цензура) | со старта p90 | от начала месяца, дн | $ | сделок")
    show = rank[:15] + ([a.main] if a.main not in rank[:15] else [])
    d = lambda x: f"{x / 24:.1f}"
    for n in show:
        r, R_ = res[n], res[n]["roll"]
        f = lambda pk: f"{d(R_[pk]['max']['median'])}/{d(R_[pk]['max']['p90'])}/{d(R_[pk]['max']['max'])} ({R_[pk]['max']['cens']})"
        print(f"{rank.index(n) + 1 if n in rank else '—'} | {n} [{r['group']}] | {f('aug')} ; {f('sep')} | "
              f"{d(R_['aug']['start']['p90'])} ; {d(R_['sep']['start']['p90'])} | {d(r['aug']['hours']['worst'])} ; {d(r['sep']['hours']['worst'])} | "
              f"{r['aug']['usd']:+.0f} ; {r['sep']['usd']:+.0f} | {r['aug']['n']} ; {r['sep']['n']}")

if __name__ == "__main__":
    main()
