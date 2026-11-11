"""Окно и перцентиль — общие для лагов, пинга и здоровья.
Делает: WINDOW_S, STATS_PERIOD_S, percentile. Не знает ни биржу, ни формат записи.
"""

WINDOW_S = 60  # окно лагов и «символ с данными за минуту»
STATS_PERIOD_S = 10  # период строки статистики шарда и фрагмента health


def percentile(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * p))]
