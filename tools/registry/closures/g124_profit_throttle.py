"""Г-124: после крупного дневного профита снижать объём. Плагин по закрытиям B1 (М5, без хеша, В-227 пакет F).
Сутки — UTC по мс закрытия. День «крупный», если его PnL ≥ q-процентиль PnL ПРЕДЫДУЩИХ суток (причинный, без заглядывания
вперёд; первые сутки порог не имеют). Следующие D суток объём × scale. Домены — спецификация Г-124 (spec-v227 §Г-124)."""
NAME = "g124"
KIND, SOURCES = "closes", ()
PARAMS = {"q": (80, 90, 95, 98), "D": (1, 2, 3, 5, 7), "scale": (0.0, 0.25, 0.5)}
DAY_MS = 86_400_000


def _quantile(xs, q):
    s = sorted(xs)
    return s[min(len(s) - 1, int(len(s) * q / 100))]


def run(closes, ctx, q, D, scale):
    days = {}
    for ms, usd in closes:
        days.setdefault(ms // DAY_MS, []).append((ms, usd))
    fills, hist, cool_until = [], [], -1
    for d in sorted(days):
        k = scale if d <= cool_until else 1.0
        fills += [(ms, usd * k) for ms, usd in days[d]]
        pnl = sum(u for _, u in days[d])
        if hist and pnl >= _quantile(hist, q):
            cool_until = d + D
        hist.append(pnl)
    return fills
