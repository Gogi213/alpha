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


H_DAYS = 5  # H правила П-07 (В-122 доп., принято Судьёй 3d3a5f6): порог «долго ждать перехая», 5 суток (было 7 — 1d2a3f4)
H_HOURS = H_DAYS * 24
PASS_FRAC = 0.10  # правило владельца «p90 ≤ 5 сут» = «доля часов t с ожиданием > H ≤ 0,10» (П-07)


def km_quantile(pairs, p):
    """Оценка квантиля p по Каплану — Мейеру для цензурированного времени ожидания.

    `pairs` — [(длительность, цензурировано?)]. Возвращает (значение, is_lower_bound). Дожитие S(u) считается
    произведением (1 − d_i/n_i) по моментам-событиям (цензурированные в риске до своего момента, затем выбывают
    без «смерти»). Квантиль — наименьшее u, где S(u) ≤ 1 − p. Если дожитие не опускается до 1 − p к концу данных,
    оценка неопределена — возвращается (максимум длительности в выборке, True): читать как «≥ значение (цензура)»."""
    if not pairs:
        return None, False
    rows = sorted(pairs, key=lambda x: x[0])
    n = len(rows)
    at_risk = n
    surv = 1.0
    thresh = (1 - p) + 1e-9
    i = 0
    result = None
    while i < n:
        t = rows[i][0]
        j = i
        d = c = 0
        while j < n and rows[j][0] == t:
            if rows[j][1]:
                c += 1
            else:
                d += 1
            j += 1
        if at_risk > 0 and d > 0:
            surv *= (1 - d / at_risk)
        at_risk -= (d + c)
        if result is None and surv <= thresh:
            result = t
        i = j
    if result is not None:
        return result, False
    return rows[-1][0], True


def rolling_kpi(closes, raw=False, h_days=5):
    """KPI, устойчивый к старту (CEO 27.09, В-120), с уточнениями Судьи по цензуре (1d2a3f4, 27.09) и по H (e6386a1,
    27.09: правило П-07 владельца — H = 5 суток, не 7). Непрерывный счёт «август + сентябрь» (без обнуления 01.09),
    сетка t — каждый час (владелец: ожидание со случайного момента по календарю, не по сделкам). «от максимума»
    (основной вид): время от t до первого закрытия, после которого счёт строго выше максимума, достигнутого к t;
    «со старта»: до первого закрытия выше значения счёта в t (как будто бот запущен в t). Данные кончаются 24.09
    00:00 — если к этому моменту нового максимума не было, время до конца считается цензурированным (истинное
    ожидание ≥ него, но неизвестно).

    Цензуру нельзя подставлять как наблюдённое значение — она делает медиану/p90/максимум заниженными у вариантов
    с высокой долей цензуры. Поэтому:
    - **главный показатель `frac_gt_h`** — доля часов t месяца, для которых точно известно, ждать ли перехая
      дольше H = `h_days` суток (без цензуры; по умолчанию 5 — правило П-07 владельца, В-122 доп., принято Судьёй
      `3d3a5f6`; было 7 — определение Судьи 1d2a3f4): только t ≤ 24.09 − H, для них окно [t; t+H] целиком лежит в
      данных, и ответ «> H или нет» не зависит от того, что будет после 24.09. `n_main` — число таких t (для доли
      цензуры в целом — `cens`, по всем t месяца, не только вошедшим в `frac_gt_h`);
    - **медиана и p90** — оценка Каплана — Мейера (`km_quantile`) по всем t месяца, с флагами `median_censored`/
      `p90_censored`: True — дожитие не опустилось до нужного уровня к концу данных, значение — не оценка, а нижняя
      граница («≥ X (цензура)»), False — точная оценка;
    - **`max`** — наибольшее время ожидания среди t месяца (цензурированное подставлено временем до конца, как
      раньше — совместимость с потребителями поля); `max_censored` — True, если оно само цензурировано (значит,
      истинный максимум месяца ещё больше и неизвестен)."""
    h_hours = h_days * 24
    ev = sorted(closes)
    s0, e0 = ms("2026-08-01"), ms("2026-09-24")
    times = [t for t, _ in ev]
    eq, c = [], 0.0
    for _t, p in ev:
        c += p
        eq.append(c)
    # следующий строгий рекорд после индекса: считаем по сетке часов двумя указателями
    import bisect
    out = {"aug": {"max": [], "start": [], "cens_max": [], "cens_start": [], "main_ok": []},
           "sep": {"max": [], "start": [], "cens_max": [], "cens_start": [], "main_ok": []}}
    # префиксный максимум
    pm, m = [], 0.0
    for v in eq:
        m = max(m, v)
        pm.append(m)
    cutoff = e0 - h_hours * MS_H  # t <= cutoff: окно [t; t+H] целиком в данных, «> H» известно без цензуры
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
        cens_max = r_max is None
        if cens_max:
            r_max = (e0 - t) / MS_H
        cens_st = r_st is None
        if cens_st:
            r_st = (e0 - t) / MS_H
        o["max"].append(r_max)
        o["cens_max"].append(cens_max)
        o["start"].append(r_st)
        o["cens_start"].append(cens_st)
        o["main_ok"].append(t <= cutoff)
        t += MS_H
    res = {}
    for pk, o in out.items():
        n = len(o["max"])
        month = {}
        for k, ck in (("max", "cens_max"), ("start", "cens_start")):
            durs, cens = o[k], o[ck]
            pairs = list(zip(durs, cens))
            med, med_c = km_quantile(pairs, 0.5)
            p90, p90_c = km_quantile(pairs, 0.9)
            mx = max(durs) if durs else None
            mx_c = cens[durs.index(mx)] if durs else False
            month[k] = {"median": med, "median_censored": med_c, "p90": p90, "p90_censored": p90_c,
                        "max": mx, "max_censored": mx_c, "cens": round(sum(cens) / n, 3) if n else None}
            if raw:
                month["raw_" + k] = durs
        n_main = sum(o["main_ok"])
        gt_h = sum(1 for ok, d in zip(o["main_ok"], o["max"]) if ok and d > h_hours)
        month["frac_gt_h"] = round(gt_h / n_main, 3) if n_main else None
        month["n_main"] = n_main
        month["h_hours"] = h_hours
        res[pk] = month
    return res


DD_MIN_USD = 10.0      # откат меньше $10 (0,4 % депозита на позиции $500) — шум, в распределение не входит
REBOUND_FRAC = 0.2     # «отскок» — подъём от текущего дна на ≥ 20 % глубины отката к этому моменту
SENS_GRID = ((10, 0.2), (5, 0.2), (20, 0.2), (10, 0.1), (10, 0.3))  # чувствительность DD_MIN×REBOUND (Судья e6386a1 п.4)
FALL_SHOW_D_USD = 50.0  # порог показа стороны падения (В-123): 2 % депозита $2500 — слово владельца, не статистика, меняемо


def iso_h(t_ms):
    return dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d %H:%M")


def hourly_underwater(closes):
    """Просадка по часу t (не по пику эпизода): пик и счёт по закрытиям ≤ t на часовой сетке, глубина = пик − счёт.
    Судья e6386a1 п.2.1: откат, начавшийся в одном месяце и продолжающийся в другом, должен быть виден в обоих —
    группировка по месяцу пика эпизода теряет глубину месяца, в котором прошло дно (у главного варианта в сентябре
    так терялось $94 → $45, у Г-85 $138 → $60)."""
    ev = sorted(closes)
    s0, e0 = ms("2026-08-01"), ms("2026-09-24")
    out = {"aug": [], "sep": []}
    i, c, pk_eq = 0, 0.0, 0.0
    t = s0
    while t < e0:
        while i < len(ev) and ev[i][0] <= t:
            c += ev[i][1]
            pk_eq = max(pk_eq, c)
            i += 1
        out["aug" if t < ms("2026-09-01") else "sep"].append(pk_eq - c)
        t += MS_H
    return out


def _episodes(ev, dmin=None, rb=None):
    """Список эпизодов отката (пик → дно → возврат) при заданных порогах — детектор вынесен из `drawdown_stats`,
    чтобы `docs/research/reviews/scripts/kpi-fall-check.py` (эталон Судьи, меняет модульные DD_MIN_USD/REBOUND_FRAC
    для чувствительности) продолжал работать без правок."""
    dmin = DD_MIN_USD if dmin is None else dmin
    rb = REBOUND_FRAC if rb is None else rb
    s0 = ms("2026-08-01")
    eps, cum = [], 0.0
    peak, t_peak = 0.0, s0
    cur = None
    for t, p in ev:
        cum += p
        if cum > peak + 1e-9:
            if cur is not None:
                cur["t_rec"] = t
                eps.append(cur)
                cur = None
            peak, t_peak = cum, t
            continue
        if cur is None:
            cur = {"t_peak": t_peak, "peak": peak, "low": cum, "t_low": t, "relows": 0, "rebounded": False}
            continue
        if cum < cur["low"] - 1e-9:
            if cur["rebounded"]:
                cur["relows"] += 1
                cur["rebounded"] = False
            cur["low"], cur["t_low"] = cum, t
        elif cum - cur["low"] >= rb * (cur["peak"] - cur["low"]):
            cur["rebounded"] = True
    if cur is not None:
        cur["t_rec"] = None
        eps.append(cur)
    return [e for e in eps if e["peak"] - e["low"] >= dmin]


def drawdown_stats(closes):
    """Сторона падения (владелец 27.09 ~04:10: «не на перехай, но не на перелоу»); уточнено Судьёй (e6386a1, 27.09).
    Непрерывный счёт «август + сентябрь» по закрытиям. Откат — от максимума счёта до возврата выше него (или до конца
    данных — не закрыт). Основные поля (`n`, `fall_*`, `depth_*`, `relows*`, `rec_*`, `open`) — по месяцу ПИКА
    эпизода, как раньше (совместимость: их читают kpi-dash-data.py, t32-grid.py, t32-exits.py). Квантили на 3–5
    эпизодах в месяце вырождаются в максимум — не единственный источник цифры, читать вместе с добавленным:

    - **`hourly`** — по часовой кривой «под водой» (`hourly_underwater`, месяц по часу t, не по пику): доля часов
      глубже $0/$25, «язва» (RMS), максимум месяца, открыт ли откат на конец месяца — не теряет глубину при переносе
      эпизода через границу месяца;
    - **`episodes`** — список эпизодов месяца пика без квантилей (даты пика/дна/возврата, глубина, перелоу, спад,
      восстановление; `open` — не закрыт к концу данных, `rec_hours` тогда `None`);
    - **`sensitivity`** — перелоу (`relows`/`relows_max`) при `SENS_GRID` порогов DD_MIN_USD×REBOUND_FRAC: владелец
      порог не выбирал, число «зависит от придуманного числа» (Судья) — печатать рядом, не одно."""
    ev = sorted(closes)
    eps_default = _episodes(ev)
    hrs = hourly_underwater(ev)
    out = {}
    for pk, (a, b) in (("aug", ("2026-08-01", "2026-09-01")), ("sep", ("2026-09-01", "2026-09-24"))):
        xs = [e for e in eps_default if ms(a) <= e["t_peak"] < ms(b)]
        fall = [(e["t_low"] - e["t_peak"]) / MS_H for e in xs]
        depth = [e["peak"] - e["low"] for e in xs]
        rec = [(e["t_rec"] - e["t_low"]) / MS_H for e in xs if e["t_rec"] is not None]
        h = hrs[pk]
        n_h = len(h)
        rms = math.sqrt(sum(x * x for x in h) / n_h) if n_h else None
        episodes_view = [{"t_peak": iso_h(e["t_peak"]), "t_low": iso_h(e["t_low"]),
                          "t_rec": iso_h(e["t_rec"]) if e["t_rec"] is not None else None,
                          "depth_usd": round(e["peak"] - e["low"], 2), "relows": e["relows"],
                          "fall_hours": round((e["t_low"] - e["t_peak"]) / MS_H, 1),
                          "rec_hours": round((e["t_rec"] - e["t_low"]) / MS_H, 1) if e["t_rec"] is not None else None,
                          "open": e["t_rec"] is None} for e in xs]
        sens = {}
        for dmin, rb in SENS_GRID:
            e2 = [e for e in _episodes(ev, dmin, rb) if ms(a) <= e["t_peak"] < ms(b)]
            sens[f"dmin{dmin}_rb{rb}"] = {"n": len(e2), "relows": sum(e["relows"] for e in e2),
                                          "relows_max": max((e["relows"] for e in e2), default=0)}
        out[pk] = {"n": len(xs), "fall_median": q(fall, 0.5), "fall_p90": q(fall, 0.9), "fall_max": max(fall) if fall else None,
                   "depth_median": q(depth, 0.5), "depth_max": max(depth) if depth else None,
                   "relows": sum(e["relows"] for e in xs), "relows_max": max((e["relows"] for e in xs), default=0),
                   "rec_median": q(rec, 0.5), "rec_max": max(rec) if rec else None, "open": sum(e["t_rec"] is None for e in xs),
                   "hourly": {"n": n_h, "d_usd": FALL_SHOW_D_USD, "frac_gt0": round(sum(x > 1e-9 for x in h) / n_h, 3) if n_h else None,
                              "frac_gtD": round(sum(x > FALL_SHOW_D_USD for x in h) / n_h, 3) if n_h else None,
                              "ulcer_usd": round(rms, 1) if rms is not None else None,
                              "max_usd": round(max(h), 1) if h else None,
                              "open_at_end": bool(h and h[-1] > DD_MIN_USD)},
                   "episodes": episodes_view, "sensitivity": sens}
    return out


def load_symbol_map(path):
    """t1_ms (закрытие) → символ, только для варианта, чьи сделки этим файлом покрыты **полностью** (сверяется на
    месте использования, ниже) — сейчас это `data/t32/main-trades.csv` (главный вариант, 658/658 закрытий совпали по
    времени). Другие варианты (Г-85, П-05, …) такой карты не имеют — для них устойчивость по монете не считается
    («нет данных»), а не подгоняется по неполному подмножеству."""
    m = {}
    if not path or not os.path.exists(path):
        return m
    import csv
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(f):
            m[int(row["t1_ms"])] = row["sym"]
    return m


def _frac_gt_h_pair(ev, h_days):
    r = rolling_kpi(ev, h_days=h_days)
    return r["aug"]["frac_gt_h"], r["sep"]["frac_gt_h"]


_NO_RANGE = {"min": {"aug": None, "sep": None}, "max": {"aug": None, "sep": None}}


def _frac_range(rests, h_days):
    """Доли `frac_gt_h` по месяцам на каждом ряду-исключении → {"min": {aug, sep}, "max": {aug, sep}}. Минимум нужен
    «не проходит» (Судья, возврат на b0f8097: остаётся > 0,10 при ЛЮБОМ исключении = минимум > 0,10), максимум —
    «проходит» (остаётся ≤ 0,10 при любом = максимум ≤ 0,10). Месяц без долей — None."""
    got = {"aug": [], "sep": []}
    for rest in rests:
        fa, fs = _frac_gt_h_pair(rest, h_days)
        if fa is not None:
            got["aug"].append(fa)
        if fs is not None:
            got["sep"].append(fs)
    return {"min": {pk: (min(v) if v else None) for pk, v in got.items()},
            "max": {pk: (max(v) if v else None) for pk, v in got.items()}}


def stability_by_day(closes, h_days=H_DAYS):
    """Судья 3d3a5f6 п.2: убрать по очереди сделки одних календарных суток (UTC) из непрерывного счёта
    «август+сентябрь», пересчитать долю `frac_gt_h` по месяцам; вернуть размах по исключениям
    {"min": {aug, sep}, "max": {aug, sep}} (возврат Судьи на b0f8097: прежде — только максимум)."""
    ev = sorted(closes)
    if not ev:
        return _NO_RANGE
    days = sorted({dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).date() for t, _ in ev})
    rests = []
    for day in days:
        d0 = int(dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc).timestamp() * 1000)
        d1 = d0 + 24 * 3_600_000
        rests.append([(t, p) for t, p in ev if not (d0 <= t < d1)])
    return _frac_range(rests, h_days)


def stability_by_symbol(closes, symbol_of, h_days=H_DAYS):
    """То же, по монетам (Судья 3d3a5f6 п.2). `symbol_of`: t1_ms → символ. Если карта не покрывает все закрытия ряда
    целиком (другой вариант, другие сделки) — возвращаются None: «не проверено», а не число по обрезку."""
    ev = sorted(closes)
    if not ev or any(t not in symbol_of for t, _ in ev):
        return _NO_RANGE
    syms = sorted({symbol_of[t] for t, _ in ev})
    return _frac_range([[(t, p) for t, p in ev if symbol_of[t] != sym] for sym in syms], h_days)


def verdict_kpi(base, stab_day, stab_sym):
    """Судья, третья сдача П-07 (`3d3a5f6`), правило 2: «проходит» — доля ≤ 0,10 в обоих месяцах и остаётся ≤ 0,10
    без сделок любых одних суток и любой одной монеты (максимум по исключениям ≤ 0,10); «не проходит» — > 0,10 хотя
    бы в одном месяце и остаётся > 0,10 при ЛЮБОМ из тех же исключений (минимум по исключениям > 0,10 — возврат
    Судьи на b0f8097: прежде сравнивался максимум, и «не проходит» выходило почти автоматически); иначе — «на
    границе». `stab_day`/`stab_sym` — размах {"min", "max"} из `stability_by_*`. Подпись обязательна: суждение по
    точке на двух месяцах, не статистика (бутстреп — справочная колонка отдельно, без порога). Монета — только если
    есть карта (иначе устойчивость — по суткам одним, признак `sym_checked`)."""
    def month_state(pk):
        b = base[pk]
        if b is None:
            return "нет данных", False
        sym_checked = stab_sym["max"][pk] is not None
        highs = [x for x in (stab_day["max"][pk], stab_sym["max"][pk]) if x is not None]
        lows = [x for x in (stab_day["min"][pk], stab_sym["min"][pk]) if x is not None]
        hi = max(highs) if highs else b
        lo = min(lows) if lows else b
        if b <= PASS_FRAC and hi <= PASS_FRAC:
            return "OK", sym_checked
        if b > PASS_FRAC and lo > PASS_FRAC:
            return "FAIL", sym_checked
        return "EDGE", sym_checked
    st_aug, sc_aug = month_state("aug")
    st_sep, sc_sep = month_state("sep")
    states = {"aug": st_aug, "sep": st_sep}
    if "нет данных" in states.values():
        v = "нет данных"
    elif st_aug == "OK" and st_sep == "OK":
        v = "проходит"
    elif "FAIL" in states.values():
        v = "не проходит"
    else:
        v = "на границе"
    note = "суждение по точке на двух месяцах, не статистика" + ("" if (sc_aug and sc_sep) else " (устойчивость по монете не проверена — нет карты символов у этого ряда)")
    return v, states, note


def _boot_days_of(closes):
    """Сутки календаря (UTC, смещение от начала месяца) → [(смещение в сутках, pnl)], по августу/сентябрю отдельно —
    порт `docs/research/reviews/scripts/kpi-share-boot.py` (Судья), не трогать тот файл (чужая зона), но мера та же:
    только справочная колонка «доля выборок с ≤ 0,10» (условие 3 П-07, без порога на прохождение)."""
    day = 86_400_000
    mon = {"aug": (ms("2026-08-01"), 31), "sep": (ms("2026-09-01"), 23)}
    out = {pk: [[] for _ in range(n)] for pk, (_s, n) in mon.items()}
    for t, p in closes:
        for pk, (s, n) in mon.items():
            d = (t - s) // day
            if 0 <= d < n:
                out[pk][int(d)].append((t - s - d * day, p))
    return out


def _boot_share(path_days, n_aug, h_days):
    day = 86_400_000
    ev, c, eq = [], 0.0, []
    for i, dday in enumerate(path_days):
        for off, p in sorted(dday):
            ev.append(i * day + off); c += p; eq.append(c)
    pm, m = [], 0.0
    for v in eq:
        m = max(m, v); pm.append(m)
    end = len(path_days) * day
    h_ms = h_days * day
    res = {"aug": [0, 0], "sep": [0, 0]}
    import bisect
    t = 0
    while t + h_ms <= end:
        i = bisect.bisect_right(ev, t)
        mx = max(pm[i - 1] if i else 0.0, 0.0)
        lim = t + h_ms
        ok_ = False
        for j in range(i, len(ev)):
            if ev[j] > lim:
                break
            if eq[j] > mx + 1e-9:
                ok_ = True; break
        pk = "aug" if t < n_aug * day else "sep"
        res[pk][0] += (not ok_); res[pk][1] += 1
        t += 3_600_000
    return {pk: a / b for pk, (a, b) in res.items() if b}


def bootstrap_share_le(closes, h_days=H_DAYS, B=200, block_days=7, seed=7):
    """Справочная колонка (условие 3 П-07): круговой блочный бутстреп суток (блок 7 сут, отдельно по месяцам, авг' +
    сен' одним путём) → доля выборок, где точечная доля `frac_gt_h` ≤ 0,10 по месяцам. Не порог, не часть вердикта —
    Судья (`3d3a5f6`): «порог 0,8 не пройдёт никто» на этой мере. Дорого (≈ как `stability_by_day`×B/дни) — считать
    только для показываемых рядов, не для всех 285."""
    import random
    rng = random.Random(seed)
    days = _boot_days_of(closes)
    ok = {"aug": 0, "sep": 0}
    for _ in range(B):
        # путь: circular block bootstrap по каждому месяцу отдельно (Судья 3d3a5f6), один путь август→сентябрь
        path = []
        for pk in ("aug", "sep"):
            d = days[pk]; n = len(d); segs = []
            while len(segs) < n:
                i = rng.randrange(n)
                segs += [d[(i + k) % n] for k in range(block_days)]
            path += segs[:n]
        r = _boot_share(path, len(days["aug"]), h_days)
        for pk in ok:
            if pk in r and r[pk] <= PASS_FRAC:
                ok[pk] += 1
    return {"aug": round(ok["aug"] / B, 3), "sep": round(ok["sep"] / B, 3), "B": B}


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
    ap.add_argument("--t32-trades", default="data/t32/main-trades.csv",
                     help="карта t1_ms → символ для устойчивости по монете (сейчас покрывает только главный вариант)")
    ap.add_argument("--verify", action="append", default=[],
                     help="имя ряда — досчитать вердикт П-07 (устойчивость+бутстреп) даже если он не в топ-15")
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
    res = {n: {"group": g, **{pk: month_metrics(ser[pk], pk) for pk in PERIODS}, "roll": rolling_kpi(ser["augsep"]),
               "dd": drawdown_stats(ser["augsep"])} for n, (g, ser) in S.items()}
    # смеси «среднее» (без занятости): пары из окон BTC и из 8 лучших по худшему месяцу $, плюс главный с каждым
    single = [n for n in S if "потолок" not in n and "одним счётом" not in n]
    top = sorted(single, key=lambda n: -min(res[n]["aug"]["usd"], res[n]["sep"]["usd"]))[:8]
    e26 = [n for n in single if n.startswith("E26 ")]
    pairs = {tuple(sorted(c)) for c in list(itertools.combinations(e26, 2)) + list(itertools.combinations(top, 2))
             + [(a.main, n) for n in top if n != a.main]}
    for c in sorted(pairs):
        ser = {pk: [(t, p / len(c)) for n in c for t, p in S[n][1][pk]] for pk in PERIODS}
        res["смесь: " + " + ".join(c)] = {"group": "смесь среднее (без занятости)", **{pk: month_metrics(ser[pk], pk) for pk in PERIODS},
                                         "roll": rolling_kpi(ser["augsep"]), "dd": drawdown_stats(ser["augsep"])}
    # N сделок (условие 4 П-07): < 10 — «сделок нет» (Δ/точка не читается), 10-29 — «описание» (не для выбора точки),
    # >= порога рейтинга (авг >= 30 / сен >= 10, короче месяц — асимметрия принята раньше) — полноценная точка
    n_note = lambda n_trades: "сделок нет" if n_trades < 10 else ("описание" if n_trades < 30 else "точка")
    ok = [n for n, r in res.items() if r["aug"]["usd"] > 0 and r["sep"]["usd"] > 0 and r["aug"]["n"] >= 30 and r["sep"]["n"] >= 10]
    # главное по Судье (1d2a3f4, 27.09; H=5 — 3d3a5f6): доля часов месяца с ожиданием перехая > H сут (без цензуры),
    # худший месяц — меньше лучше; p90/максимум «от максимума» (Каплан — Мейер) — справочно, вторым ключом
    key = lambda n: (max(res[n]["roll"]["aug"]["frac_gt_h"] or 0.0, res[n]["roll"]["sep"]["frac_gt_h"] or 0.0),
                      max(res[n]["roll"]["aug"]["max"]["p90"], res[n]["roll"]["sep"]["max"]["p90"]))
    rank = sorted(ok, key=key)
    # вердикт П-07 (условия 1-4, Судья 3d3a5f6) — дорого (устойчивость по суткам ~54 пересчёта на ряд, бутстреп 400):
    # считать только для показываемых рядов (топ-15 + главный + --verify), не для всех рядов рейтинга
    show = rank[:15] + [n for n in ([a.main] + a.verify) if n not in rank[:15] and n in res]
    symbol_of = load_symbol_map(a.t32_trades)
    for n in show:
        ser = S.get(n)
        closes = ser[1]["augsep"] if ser else None
        if closes is None:  # смесь: собрать заново тем же способом, что и выше
            parts = n[len("смесь: "):].split(" + ") if n.startswith("смесь: ") else None
            closes = [(t, p / len(parts)) for pn in parts for t, p in S[pn][1]["augsep"]] if parts else None
        if closes is None:
            continue
        base = {pk: res[n]["roll"][pk]["frac_gt_h"] for pk in ("aug", "sep")}
        n_main = {pk: res[n]["roll"][pk]["n_main"] for pk in ("aug", "sep")}
        stab_day = stability_by_day(closes)
        stab_sym = stability_by_symbol(closes, symbol_of)
        v, states, note = verdict_kpi(base, stab_day, stab_sym)
        boot = bootstrap_share_le(closes)
        res[n]["kpi07"] = {"frac": base, "n_main": n_main,
                            "stability_day_max": stab_day["max"], "stability_symbol_max": stab_sym["max"],
                            "stability_day_min": stab_day["min"], "stability_symbol_min": stab_sym["min"],
                            "states": states, "verdict": v, "note": note, "boot_le010": boot,
                            "n_trades_note": {pk: n_note(res[n][pk]["n"]) for pk in ("aug", "sep")}}
    json.dump({"definition": __doc__.split("\n\n")[1], "main": a.main, "n_rows": len(res), "n_pairs": len(pairs),
               "rank": rank, "results": res}, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
    h = lambda x: "—" if x is None else f"{x:.0f}"
    print(f"рядов {len(res)} (смесей-среднее {len(pairs)}); плюс в обоих месяцах и сделок достаточно: {len(ok)}")
    print(f"главное (правило П-07, Судья 3d3a5f6): доля часов месяца с ожиданием перехая > H={H_HOURS / 24:.0f} сут "
          f"(без цензуры, t ≤ 24.09−H; n_main — число таких t); порог ≤ {PASS_FRAC:.2f} в обоих месяцах")
    print("место | вариант | доля > H [авг(n_main) ; сен(n_main)] | вердикт (устойч. сутки/монета: мин–макс доли) | "
          "бутстреп ≤0,10 [авг;сен] | скользящий старт от максимума, дн (К-М, ⚠=цензура) [авг ; сен] | $ | сделок")
    d = lambda x: "—" if x is None else f"{x / 24:.1f}"
    star = lambda flag: "⚠" if flag else ""
    for n in show:
        r, R_ = res[n], res[n]["roll"]
        gt = lambda pk: f"{R_[pk]['frac_gt_h']:.0%}({R_[pk]['n_main']})" if R_[pk]['frac_gt_h'] is not None else "—"
        f = lambda pk: (f"{d(R_[pk]['max']['median'])}{star(R_[pk]['max']['median_censored'])}/"
                        f"{d(R_[pk]['max']['p90'])}{star(R_[pk]['max']['p90_censored'])}/"
                        f"{d(R_[pk]['max']['max'])}{star(R_[pk]['max']['max_censored'])} ({R_[pk]['max']['cens']})")
        k07 = r.get("kpi07")
        if k07:
            rng = lambda lo, hi, pk: "—" if hi[pk] is None else f"{lo[pk]:.0%}–{hi[pk]:.0%}"
            sd, sdl = k07["stability_day_max"], k07["stability_day_min"]
            ss, ssl = k07["stability_symbol_max"], k07["stability_symbol_min"]
            sym_txt = f", мон {rng(ssl, ss, 'aug')}/{rng(ssl, ss, 'sep')}" if ss["aug"] is not None else ""
            vtxt = f"{k07['verdict']} (сут {rng(sdl, sd, 'aug')}/{rng(sdl, sd, 'sep')}{sym_txt})"
            boottxt = f"{k07['boot_le010']['aug']:.2f};{k07['boot_le010']['sep']:.2f}"
        else:
            vtxt = boottxt = "—"
        print(f"{rank.index(n) + 1 if n in rank else '—'} | {n} [{r['group']}] | {gt('aug')} ; {gt('sep')} | {vtxt} | {boottxt} | "
              f"{f('aug')} ; {f('sep')} | {r['aug']['usd']:+.0f} ; {r['sep']['usd']:+.0f} | {r['aug']['n']} ; {r['sep']['n']}")

if __name__ == "__main__":
    main()
