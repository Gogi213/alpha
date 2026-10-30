#!/usr/bin/env python3
"""TK-461 / P-TK461 §10 «Алгоритм»: быстрый симулятор сделки — то же правило, что tk461_sim.trade, но без цикла по минутам:
окно [e,last] считается массивами (кумулятивный максимум high -> уровень стопа/трейла перед минутой, первая минута пересечения
через argmax). Формы: стоп (в т.ч. -inf), цель-лимит, трейл, частичная + трейл остатка, безубыток (be). Свинг и ОТ — в эталоне.
Выход тот же кортеж (legs, m, reason, mfe); значения выбираются из тех же float64 -> гейт «байт в байт» против tk461_sim.trade.
Окно с нечисловыми значениями — откат на эталон."""
import numpy as np
import tk461_sim as S


def _first(mask):
    k = int(mask.argmax())
    return k if mask[k] else -1


def trade_fast(o, h, l, c, e, last, entry, stop, tgt=None, trail=None, swing=None, part=None, be=None, bar_end=(), ot=None):
    if swing is not None or ot is not None:
        return S.trade(o, h, l, c, e, last, entry, stop, tgt, trail, swing, part, be, bar_end, ot)
    H, L, O = h[e:last + 1], l[e:last + 1], o[e:last + 1]
    if not (np.isfinite(H).all() and np.isfinite(L).all() and np.isfinite(O).all()):
        return S.trade(o, h, l, c, e, last, entry, stop, tgt, trail, swing, part, be, bar_end, ot)
    n = len(H)
    ext = np.maximum.accumulate(H)
    prev_ext = np.empty(n)  # экстремум до минуты k (с конца k-1)
    prev_ext[0] = -np.inf
    prev_ext[1:] = ext[:-1]
    cur = np.full(n, float(stop))  # уровень стопа перед минутой k
    if trail is not None:
        np.maximum(cur, prev_ext - trail, out=cur)
    kp = -1
    if part is not None:
        kp = _first(H > part[0])
        if kp >= 0 and part[1] < 1.0:
            sl = slice(kp + 1, n)
            cur[sl] = np.maximum(cur[sl], prev_ext[sl] - part[2])
    if be is not None:
        kb = _first(H >= be)
        if kb >= 0:
            sl = slice(kb + 1, n)
            cur[sl] = np.maximum(cur[sl], entry)
    ks = _first(L <= cur)
    kt = _first(H > tgt) if tgt is not None else -1
    pos = 1.0
    legs = []
    if ks >= 0 and (kt < 0 or ks <= kt):
        if kp >= 0 and kp < ks:
            legs.append((part[1], part[0], True)); pos -= part[1]
        px = min(cur[ks], O[ks]) if ks > 0 else min(cur[ks], entry)
        legs.append((pos, px, False))
        return legs, e + ks, S.STOP, max(entry, ext[ks - 1]) if ks > 0 else entry
    if kt >= 0:
        if kp >= 0 and kp < kt:
            legs.append((part[1], part[0], True)); pos -= part[1]
        legs.append((pos, tgt, True))
        return legs, e + kt, S.TARGET, max(entry, ext[kt - 1]) if kt > 0 else entry
    if kp >= 0:
        legs.append((part[1], part[0], True)); pos -= part[1]
    legs.append((pos, c[last], False))
    return legs, last, S.DEADLINE, max(entry, ext[-1])
