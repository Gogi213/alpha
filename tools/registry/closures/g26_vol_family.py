"""Г-26: семейства по волатильности монеты. Плагин KIND='keep' (М5, В-227 пакет F): сигнал остаётся, если σ240 монеты на момент t0
(последнее окно с window_end_ms <= t0) попадает в корзину bucket из nq квантильных (nq=3 терцили, 5 квинтили). Границы корзин — квантили
σ той же монеты за ПРОШЛЫЕ сутки (окна до 00:00 UTC суток сигнала; Судья 04:05: причинно) — не по всему месяцу. Без истории (< MIN_HIST окон)
или без σ на t0 сигнал не классифицируется и исключается (выбор Инженера, Судье отметить). Комбинацию с Г-13/Г-24/Г-25 задаёт сетка П-15."""
import bisect
import calendar
import time

NAME = "g26"
KIND, SOURCES = "keep", ("sigma",)
PARAMS = {"nq": (3, 5), "bucket": (0, 1, 2, 3, 4)}
MIN_HIST = 240   # окон по минуте = 4 ч истории


def _day_start_ms(day):
    return calendar.timegm(time.strptime(day, "%Y-%m-%d")) * 1000


def run(data, ctx, nq, bucket):
    if bucket >= nq:
        return []
    series, bounds, keep = {}, {}, []
    for d in data:
        sym, day = d["symbol"], d["day_utc"]
        if sym not in series:
            s = sorted(ctx.sigma(sym))
            series[sym] = ([t for t, _ in s], [v for _, v in s])
        ts, vs = series[sym]
        if (sym, day) not in bounds:
            hist = sorted(vs[:bisect.bisect_left(ts, _day_start_ms(day))])
            bounds[(sym, day)] = None if len(hist) < MIN_HIST else [hist[len(hist) * i // nq] for i in range(1, nq)]
        b = bounds[(sym, day)]
        i = bisect.bisect_right(ts, int(d["t0_ns"]) // 1_000_000) - 1
        if b is None or i < 0:
            continue
        if bisect.bisect_right(b, vs[i]) == bucket:
            keep.append((sym, day, d["form"], d["signal_index"]))
    return keep
