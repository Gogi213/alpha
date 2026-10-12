"""Г-111: цель = доля f предыдущего импульса (exit-sim, upper bound: минутные свечи, касание цели = исполнение мейкером, без очереди).

Импульс = размах [low, high] минутных свечей за окно w мин до t0 круга (только прошлое); цель лонга = low + f*(high-low). Выход по цене цели на первой
минуте после t0, где high >= цели, если это раньше исходного exit_ns и цель выше entry_vwap. Окно и доля - домен спеки; назначение - протокол П-15."""
import bisect

NAME = "g111_impulse_target"
KIND, SOURCES = "rounds", ("klines",)
PARAMS = {"w": (15, 30, 60, 120, 240), "f": (0.25, 1 / 3, 0.5, 2 / 3, 0.75)}
_cache = {}


def _kl(ctx, sym):
    k = (id(ctx), sym)
    if k not in _cache:
        kl = ctx.klines(sym)
        _cache[k] = ([r[0] for r in kl], [r[2] for r in kl], [r[3] for r in kl])
    return _cache[k]


def run(data, ctx, w, f):
    out = {}
    for r in data:
        if int(r["dir"]) != 1:
            continue
        ts, hi, lo = _kl(ctx, r["symbol"])
        t0, ex = int(r["t0_ns"]) // 1_000_000, int(r["exit_ns"]) // 1_000_000
        i = bisect.bisect_right(ts, t0)                     # первая минута, открывшаяся после t0
        e = bisect.bisect_right(ts, t0 - 60_000)            # окно импульса - только минуты, закрытые до t0 (причинно)
        a = bisect.bisect_left(ts, t0 - w * 60_000)
        if i >= len(ts) or e - a < 1:
            continue
        top, bot = max(hi[a:e]), min(lo[a:e])
        tgt = bot + f * (top - bot)
        if tgt <= float(r["entry_vwap"]):
            continue
        while i < len(ts) and ts[i] + 60_000 <= ex:
            if hi[i] >= tgt:
                out[(r["symbol"], r["day_utc"], r["form"], r["signal_index"])] = (max(ts[i], t0 + 1) * 1_000_000, tgt, False, "impulse_target")
                break
            i += 1
    return out
