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


H_HOURS = 7 * 24  # H владельца/Судьи (принято 1d2a3f4): порог «долго ждать перехая», 7 суток


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


def rolling_kpi(closes, raw=False, h_days=7):
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
      дольше H = `h_days` суток (без цензуры; по умолчанию 7 — определение Судьи 1d2a3f4, для правила П-07 — 5,
      В-122): только t ≤ 24.09 − H, для них окно [t; t+H] целиком лежит в данных, и ответ «> H или нет» не зависит от
      того, что будет после 24.09. `n_main` — число таких t (для доли цензуры в целом — `cens`, по всем t месяца, не
      только вошедшим в `frac_gt_h`);
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
                   "hourly": {"n": n_h, "frac_gt0": round(sum(x > 1e-9 for x in h) / n_h, 3) if n_h else None,
                              "frac_gt25": round(sum(x > 25 for x in h) / n_h, 3) if n_h else None,
                              "ulcer_usd": round(rms, 1) if rms is not None else None,
                              "max_usd": round(max(h), 1) if h else None,
                              "open_at_end": bool(h and h[-1] > DD_MIN_USD)},
                   "episodes": episodes_view, "sensitivity": sens}
    return out


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
    ok = [n for n, r in res.items() if r["aug"]["usd"] > 0 and r["sep"]["usd"] > 0 and r["aug"]["n"] >= 30 and r["sep"]["n"] >= 10]
    # главное по Судье (1d2a3f4, 27.09): доля часов месяца с ожиданием перехая > H = 7 сут (без цензуры), худший месяц —
    # меньше лучше; p90/максимум «от максимума» (Каплан — Мейер) — справочно, вторым ключом (прежний порядок, до 27.09 ~05:00)
    key = lambda n: (max(res[n]["roll"]["aug"]["frac_gt_h"] or 0.0, res[n]["roll"]["sep"]["frac_gt_h"] or 0.0),
                      max(res[n]["roll"]["aug"]["max"]["p90"], res[n]["roll"]["sep"]["max"]["p90"]))
    rank = sorted(ok, key=key)
    json.dump({"definition": __doc__.split("\n\n")[1], "main": a.main, "n_rows": len(res), "n_pairs": len(pairs),
               "rank": rank, "results": res}, open(a.out, "w", encoding="utf-8", newline=""), ensure_ascii=False, indent=1)
    h = lambda x: "—" if x is None else f"{x:.0f}"
    print(f"рядов {len(res)} (смесей-среднее {len(pairs)}); плюс в обоих месяцах и сделок достаточно: {len(ok)}")
    print(f"главное (Судья 1d2a3f4): доля часов месяца с ожиданием перехая > H={H_HOURS / 24:.0f} сут (без цензуры, t ≤ 24.09−H; n_main — число таких t)")
    print("место | вариант | доля > H [авг(n_main) ; сен(n_main)] | скользящий старт от максимума, дн (К-М, ⚠=цензура) [авг ; сен]: медиана/p90/макс (доля цензуры) | $ | сделок")
    show = rank[:15] + ([a.main] if a.main not in rank[:15] else [])
    d = lambda x: "—" if x is None else f"{x / 24:.1f}"
    star = lambda flag: "⚠" if flag else ""
    for n in show:
        r, R_ = res[n], res[n]["roll"]
        gt = lambda pk: f"{R_[pk]['frac_gt_h']:.0%}({R_[pk]['n_main']})" if R_[pk]['frac_gt_h'] is not None else "—"
        f = lambda pk: (f"{d(R_[pk]['max']['median'])}{star(R_[pk]['max']['median_censored'])}/"
                        f"{d(R_[pk]['max']['p90'])}{star(R_[pk]['max']['p90_censored'])}/"
                        f"{d(R_[pk]['max']['max'])}{star(R_[pk]['max']['max_censored'])} ({R_[pk]['max']['cens']})")
        print(f"{rank.index(n) + 1 if n in rank else '—'} | {n} [{r['group']}] | {gt('aug')} ; {gt('sep')} | {f('aug')} ; {f('sep')} | "
              f"{r['aug']['usd']:+.0f} ; {r['sep']['usd']:+.0f} | {r['aug']['n']} ; {r['sep']['n']}")

if __name__ == "__main__":
    main()
