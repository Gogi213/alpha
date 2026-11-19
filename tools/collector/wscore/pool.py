"""Правило пула G-022, одно на все биржи: оборот за сутки не ниже порога, без top_exclude крупнейших по обороту.
Делает: rank_pool(qv, min_qv, top_exclude) -> (пул, исключённые топ); balanced_chunks(items, per_conn) -> куски не больше per_conn и равного размера. Не знает биржу: отбор торгуемых контрактов и источник оборота — в адаптере.
"""


def rank_pool(qv, min_qv, top_exclude):
    """qv — {символ: оборот за сутки, $} уже отобранных контрактов. Топ — по убыванию оборота (при равенстве — по имени); пул — отсортированные имена с оборотом ≥ min_qv."""
    ranked = sorted(qv, key=lambda x: (-qv[x], x))
    top = ranked[:top_exclude]
    return sorted(x for x in ranked[top_exclude:] if qv[x] >= min_qv), top


def balanced_chunks(items, per_conn):
    """Куски не длиннее per_conn и почти равного размера. Хвост из нескольких символов (80+80+…+8) — разреженный шард: пауза данных дольше порога для него естественна (TK-532, замер 12.11: хвост из 8 символов дал 26 событий gap за 6 мин)."""
    if not items:
        return []
    n = -(-len(items) // per_conn)
    size = -(-len(items) // n)
    return [items[i : i + size] for i in range(0, len(items), size)]
