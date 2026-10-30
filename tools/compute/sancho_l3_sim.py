#!/usr/bin/env python3
"""TK-455 / P-TK455 §2, §10: симулятор пути сделки по минутам (ядро слоя 3, без данных и издержек).
Все цены — в «длинной» ориентации: шорт зеркалится знаком (o,h,l,c -> -o,-l,-h,-c), вход/стоп/цель — в тех же знаковых ценах.
Правила: стоп и цель в одной минуте — стоп первым; стоп по худшему из уровня и open минуты (гэп); цель-лимит — только при
проходе насквозь (high > цель), исполнение по цене цели; E1: лимит на L исполнен, если за окно минута прошла насквозь
(low < L для лонга), цена исполнения min(L, open); минута исполнения: стоп считается, цель — нет (поправка §10 п.2);
дедлайн — выход по close последней минуты дедлайн-бара (тейкер). Один скан на сигнал: cummin/cummax + searchsorted.
"""
import numpy as np

STOP, TARGET, DEADLINE, NOFILL = 0, 1, 2, 3


def mirror(o, h, l, c, d):
    return (o, h, l, c) if d > 0 else (-o, -l, -h, -c)


def fill_e1(o, l, e0, wmin, level, stop):
    """первая минута m in [e0, e0+wmin), где l[m] < level (насквозь). Заявка снимается, если раньше достигнут стоп-уровень
    (в минуте до исполнения; в минуте исполнения стоп считается уже как сделка). Возврат (m, цена) либо None."""
    hi = min(len(l), e0 + wmin)
    if hi <= e0:
        return None
    seg = l[e0:hi]
    thr = np.nonzero(seg < level)[0]
    if not len(thr):
        return None
    m = e0 + int(thr[0])
    pre = l[e0:m]
    if len(pre) and (pre <= stop).any():   # стоп достигнут раньше исполнения (он ниже уровня, значит прошёл бы и через L)
        return None
    return m, min(level, o[m])


def trade(o, h, l, c, e, last, entry_px, stop, tgt, e1=False):
    """сделка, вход в минуте e (E0: по open e; E1: исполнено в e). Скан минут e..last. tgt=None — без цели (форма A:
    stop=-inf). Возврат (причина, минута выхода, цена выхода)."""
    ls = l[e:last + 1]
    cmin = np.minimum.accumulate(ls)
    si = int(np.searchsorted(-cmin, -stop, side="left")) if np.isfinite(stop) else len(ls)
    ti = len(ls)
    if tgt is not None:
        ts = e + (1 if e1 else 0)
        if ts <= last:
            cmax = np.maximum.accumulate(h[ts:last + 1])
            k = int(np.searchsorted(cmax, tgt, side="right"))
            ti = k + (ts - e) if k < len(cmax) else len(ls)
    if si < len(ls) and si <= ti:
        m = e + si
        return STOP, m, min(stop, o[m]) if m > e or not e1 else min(stop, entry_px)
    if ti < len(ls):
        return TARGET, e + ti, tgt
    return DEADLINE, last, c[last]


def gross_bps(entry_px, exit_px):
    return 1e4 * (exit_px - entry_px) / abs(entry_px)
