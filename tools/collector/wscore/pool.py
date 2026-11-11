"""Правило пула G-022, одно на все биржи: оборот за сутки не ниже порога, без top_exclude крупнейших по обороту.
Делает: rank_pool(qv, min_qv, top_exclude) -> (пул, исключённые топ). Не знает биржу: отбор торгуемых контрактов и источник оборота — в адаптере.
"""


def rank_pool(qv, min_qv, top_exclude):
    """qv — {символ: оборот за сутки, $} уже отобранных контрактов. Топ — по убыванию оборота (при равенстве — по имени); пул — отсортированные имена с оборотом ≥ min_qv."""
    ranked = sorted(qv, key=lambda x: (-qv[x], x))
    top = ranked[:top_exclude]
    return sorted(x for x in ranked[top_exclude:] if qv[x] >= min_qv), top
