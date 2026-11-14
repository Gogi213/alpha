"""П-42: детерминированный блочный бутстреп — зерно от самого ряда, чтобы один и тот же ряд давал один и тот же интервал в любых таблицах (замечание проверки 14.11, п. 5).

Использование: `import detstat; detstat.patch(report_tick)` — подменяет `stat` в модуле report_tick (его использует `halves_stats`); `detstat.stat(x)` — напрямую.
"""
import hashlib

import numpy as np

import lib


def stat(x, rng=None):
    x = np.asarray(x, dtype=float)
    seed = int(hashlib.md5(np.round(x, 9).tobytes()).hexdigest()[:8], 16)
    return lib.stat(x, np.random.default_rng(seed))


def patch(*mods):
    for m in mods:
        m.stat = stat
