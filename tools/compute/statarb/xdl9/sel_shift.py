#!/usr/bin/env python3
"""TK-493 S1 (П-37 r3.1 §7.2): сдвиги партнёра {+1, +2, -1} суток для эмпирического нуля A1 и их общее пересечение.
Только stdlib (импортируется и ПК-скриптами, и Deck-подготовкой панелей). Только ФОРМИРОВАНИЕ 01.10-14.10: любой источник сдвига
обязан лежать в [D_FORM0, D_TRADE0); сутки >= D_TRADE0 (торговое окно) ни как цель, ни как источник не возникают.

Две единицы сдвига (П-37 §7.2, отступление F7 «вне крипты - торговый день»):
  cal   - календарные сутки (крипта, 24/7): цель d берёт источник d - k.
  trade - торговые дни (все остальные полосы): позиция pos(d) = число будних суток в [D_FORM0, d); для будних суток это индекс
          в списке будних T, для субботы и воскресенья - индекс СЛЕДУЮЩЕГО понедельника (число будних суток до них). Цель d берёт
          источник T[pos(d) - k]. Для k = +1 это в точности правило F7 (понедельник <- пятница, суббота и воскресенье <- пятница,
          иначе предыдущие будние сутки); для k = +2 - вторые будние сутки назад; для k = -1 - следующие будние сутки.
Общее пересечение: сутки-цели, у которых источник существует при ВСЕХ сдвигах из SHIFTS. Для обеих единиц оно даёт 03.10-13.10
(сутки 2467-2477, 11 календарных суток); в торговых днях у полос не-крипты - 7 будних суток (05-09.10 и 12-13.10) и 4 выходных.
Требование CEO: не меньше 10 суток (календарных), иначе код отчитывается (check_span).
"""
D_FORM0, D_TRADE0 = 2465, 2479          # сутки от 2020-01-01 UTC: 01.10 и 15.10 (торговое окно с D_TRADE0 НЕ читается)
SHIFTS = (1, 2, -1)
TAG = {1: "p1", 2: "p2", -1: "m1"}       # метка сдвига в именах каталогов и файлов
MIN_SPAN_DAYS = 10                      # требование CEO (r3.1): общее пересечение не короче 10 суток


def is_weekday(d):
    """2020-01-01 - среда: weekday = (d + 2) % 7, 0 = понедельник."""
    return (d + 2) % 7 < 5


WEEKDAYS = [d for d in range(D_FORM0, D_TRADE0) if is_weekday(d)]


def pos(d):
    """Число будних суток в [D_FORM0, d)."""
    return sum(1 for w in WEEKDAYS if w < d)


def source_day(d, k, mode):
    """Сутки-источник для цели d при сдвиге партнёра на +k (k > 0: партнёр взят из прошлого) или None."""
    if not D_FORM0 <= d < D_TRADE0:
        return None
    if mode == "cal":
        s = d - k
        return s if D_FORM0 <= s < D_TRADE0 else None
    if mode == "trade":
        j = pos(d) - k
        return WEEKDAYS[j] if 0 <= j < len(WEEKDAYS) else None
    raise ValueError("mode: cal | trade")


def common_days(mode, shifts=SHIFTS):
    return [d for d in range(D_FORM0, D_TRADE0) if all(source_day(d, k, mode) is not None for k in shifts)]


def common_window(mode, shifts=SHIFTS):
    """-> (первые сутки, сутки после последних): окно должно быть непрерывным (его читает form: window_days суток перед from_day)."""
    ds = common_days(mode, shifts)
    if not ds or ds[-1] - ds[0] + 1 != len(ds):
        raise ValueError("общее пересечение сдвигов не непрерывно: %r" % ds)
    return ds[0], ds[-1] + 1


def check_span(mode, shifts=SHIFTS):
    """-> (суток, будних суток, ok): ok = суток >= MIN_SPAN_DAYS."""
    a, b = common_window(mode, shifts)
    return b - a, sum(1 for d in range(a, b) if is_weekday(d)), (b - a) >= MIN_SPAN_DAYS


def mode_of(slug):
    return "cal" if slug == "crypto" else "trade"


def form_args(mode):
    """Аргументы `statarb form`: окно = window_days суток перед from_day."""
    a, b = common_window(mode)
    return dict(from_day=b, to_day=b + 1, window_days=b - a)


if __name__ == "__main__":
    for m in ("cal", "trade"):
        a, b = common_window(m)
        print(m, "окно суток [%d, %d)" % (a, b), "суток %d, будних %d, ok=%s" % check_span(m), form_args(m))
