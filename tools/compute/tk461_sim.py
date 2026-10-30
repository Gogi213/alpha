#!/usr/bin/env python3
"""TK-461 / P-TK461 §4, §9 п.2: эталонный симулятор сделки по минутам с трейлом, частичной фиксацией, безубытком, отменой сценария.
Ядро без данных и издержек; ориентация «длинная» (шорт зеркалится как в sancho_l3_sim.mirror). Эталон: прямой цикл по минутам;
быстрый вариант (один скан на событие, все ячейки) сверяется с ним до знака в шаге C (P-TK461 §10 «Алгоритм»).
Порядок внутри минуты m: (1) стоп (уровень с конца m-1; гэп — по худшему из уровня и open), (2) цель-лимит (только насквозь, high > цены),
(3) обновление экстремума/трейла/БУ с high[m] — действует со следующей минуты, (4) если m — последняя минута бара ТФ: проверка ОТ
по закрытию (выход по market, цена = close минуты m, тейкер), (5) дедлайн — close последней минуты (тейкер). Стоп и цель в одной минуте — стоп.
Ноги: (доля, цена выхода, maker?) — цель/частичная = лимит (maker), остальное = тейкер."""
import numpy as np

STOP, TARGET, DEADLINE, OT, PART_STOP = "stop", "target", "deadline", "ot", "stop"


def trade(o, h, l, c, e, last, entry, stop, tgt=None, trail=None, swing=None, part=None, be=None,
          bar_end=(), ot=None):
    """o,h,l,c — минутные массивы (длинная ориентация); e — минута входа (вход по `entry`, E0: open e); last — последняя минута
    дедлайн-бара. stop — цена (-inf: без ценового стопа, форма ST); tgt — цена лимитной цели или None;
    trail — расстояние c·ATR (цена) от экстремума, стоп только вверх; swing — массив уровней по минутам (стоп подтягивается на
    swing[m] с минуты m+1; NaN — нет), TR-свинг; part=(pt, frac, trail_dist): frac позиции лимитом на цене pt, остаток под трейлом
    trail_dist от экстремума (экстремум — с входа; трейл активен после частичной); be — цена «+k·R», после её достижения
    стоп = max(стоп, entry); bar_end — множество минут-концов баров ТФ в [e, last]; ot(m, st) -> bool, st = dict(mfe=макс high с
    входа, since=число баров ТФ подряд без нового максимума до этого бара включительно, c=close m)."""
    legs, pos = [], 1.0
    ext = -np.inf
    cur = stop
    part_done = part is None
    bars_since, mfe_prev = 0, entry
    bar_set = set(bar_end)
    mfe = entry
    reason = DEADLINE
    for m in range(e, last + 1):
        # (1) стоп
        if np.isfinite(cur) and l[m] <= cur:
            px = min(cur, o[m]) if m > e else min(cur, entry)
            legs.append((pos, px, False)); return legs, m, STOP, mfe
        # (2) цель / частичная
        if tgt is not None and h[m] > tgt:
            legs.append((pos, tgt, True)); return legs, m, TARGET, mfe
        if not part_done and h[m] > part[0]:
            legs.append((part[1], part[0], True)); pos -= part[1]; part_done = True
        # (3) экстремум, трейл, БУ — со следующей минуты
        if h[m] > ext:
            ext = h[m]
        if h[m] > mfe:
            mfe = h[m]
        if trail is not None:
            cur = max(cur, ext - trail)
        if part is not None and part_done and part[1] < 1.0:
            cur = max(cur, ext - part[2])
        if swing is not None and np.isfinite(swing[m]):
            cur = max(cur, swing[m])
        if be is not None and h[m] >= be:
            cur = max(cur, entry)
        # (4) ОТ на закрытии бара ТФ
        if m in bar_set:
            bars_since = 0 if mfe > mfe_prev else bars_since + 1
            mfe_prev = mfe
            if ot is not None and ot(m, dict(mfe=mfe, since=bars_since, c=c[m])):
                legs.append((pos, c[m], False)); return legs, m, OT, mfe
    legs.append((pos, c[last], False))
    return legs, last, DEADLINE, mfe


def gross_bps(legs, entry):
    return sum(f * 1e4 * (px - entry) / abs(entry) for f, px, _ in legs)


def fee_bps(legs, entry_maker, mk=1.26, tk=3.15):
    """комиссия, bps позиции: вход (maker/taker) + ноги; тариф В-63 по ногам (src/lob/costs.rs)."""
    return (mk if entry_maker else tk) + sum(f * (mk if m else tk) for f, _, m in legs)
