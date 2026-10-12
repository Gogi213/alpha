"""Г-130: стоп B1 за круглым числом тиков (exit-sim, upper bound: минутные свечи, касание = исполнение, без очереди и проскальзывания).

Меняются только лонг-круги с reason=stop (прочие не задеты). S0 = entry_vwap*(1-2 %) (stop_form pct2); тик = entry_px/price_tick из signals.csv, притянутый
к ближайшему {1,2,5}*10^k (оценка: цена сигнала чуть выше уровня). R = круглое 10^j тиков не выше S0, S' = (R - d) тиков. По 1м свечам с минуты исходного
стопа до t0+14400 с: low <= S' -> выход S' тейкером (reason stop_round); раньше или в той же минуте high >= entry_vwap*1.01 (активация трейла tr1x1)
-> трейл НЕ моделируется, берётся его нижняя граница entry_vwap*1.01*0.99 тейкером (reason trail_floor); иначе close минуты дедлайна тейкером (reason deadline_round).
Монеты без свечей и круги, чьи свечи кончились раньше дедлайна, остаются как есть."""
import bisect
import math

NAME = "g130_stop_round"
KIND, SOURCES = "rounds", ("klines", "signals")
PARAMS = {"d": (0, 1, 2, 3, 5, 8, 13, 21), "j": (2, 3, 4)}
STOP_PCT, ACT_PCT, TRAIL_PCT, DEADLINE_S = 2.0, 1.0, 1.0, 14400
_cache = {}


def snap_tick(x):
    """Ближайшее к x число вида {1,2,5}*10^k (по логарифму)."""
    e = math.floor(math.log10(x))
    return min((m * 10.0 ** k for k in (e, e + 1) for m in (1, 2, 5)), key=lambda c: abs(math.log(c / x)))


def _ticks(ctx):
    k = ("sig", id(ctx))
    if k not in _cache:
        _cache[k] = {(s["symbol"], s["day_utc"], s["form"], s["signal_index"]): snap_tick(float(s["entry_px"]) / int(s["price_tick"]))
                     for s in ctx.signals() if s["price_tick"] and int(s["price_tick"]) > 0}
    return _cache[k]


def _kl(ctx, sym):
    k = (id(ctx), sym)
    if k not in _cache:
        _cache[k] = [r for r in ctx.klines(sym)]
    return _cache[k]


def run(data, ctx, d, j):
    out, ticks = {}, _ticks(ctx)
    for r in data:
        key = (r["symbol"], r["day_utc"], r["form"], r["signal_index"])
        if int(r["dir"]) != 1 or r["reason"] != "stop" or key not in ticks:
            continue
        kl = _kl(ctx, r["symbol"])
        if not kl:
            continue
        tick, ev = ticks[key], float(r["entry_vwap"])
        s0 = ev * (1 - STOP_PCT / 100.0)
        step = 10 ** j
        s1 = (math.floor(s0 / tick / step + 1e-9) * step - d) * tick
        t0, ex = int(r["t0_ns"]) // 1_000_000, int(r["exit_ns"]) // 1_000_000
        end = t0 + DEADLINE_S * 1000
        ts = [k[0] for k in kl]
        i = max(bisect.bisect_right(ts, ex) - 1, 0)       # минута исходного стопа
        act, floor_px = ev * (1 + ACT_PCT / 100.0), ev * (1 + ACT_PCT / 100.0) * (1 - TRAIL_PCT / 100.0)
        last = None
        while i < len(kl) and ts[i] + 60_000 <= end:
            _, _, hi, lo, cl = kl[i]
            if hi >= act:                                 # в одну минуту оба - в пользу активации
                out[key] = ((ts[i] + 60_000) * 1_000_000, floor_px, True, "trail_floor"); break
            if lo <= s1:
                out[key] = (max(ts[i], ex) * 1_000_000 + 1, s1, True, "stop_round"); break
            last = i; i += 1
        else:
            if last is not None and ts[last] + 120_000 > end:   # свечи дошли до дедлайна
                out[key] = ((ts[last] + 60_000) * 1_000_000, kl[last][4], True, "deadline_round")
    return out
