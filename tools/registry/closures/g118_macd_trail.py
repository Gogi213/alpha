"""Г-118: трейлинг по падению гистограммы MACD(12,26,9) на 1м close (exit-sim, upper bound: минутные свечи, без очереди, выход тейкером по close).

Падение = пик гистограммы с t0 круга минус текущее значение; порог = квантиль q падений от бегущего пика гистограммы монеты за ПРОШЛЫЕ сутки UTC
(причинно, как Г-124/Г-26). Круг выходит на close первой минуты после t0, где падение >= порога, если это раньше исходного exit_ns. Старт отсчёта — t0_ns
(момент заливки входа в rounds.csv не пишется — отметить в протоколе)."""
import bisect

NAME = "g118_macd_trail"
KIND, SOURCES = "rounds", ("klines",)
PARAMS = {"q": (5, 15, 25, 35, 50, 65, 75, 85, 95)}
FAST, SLOW, SIG = 12, 26, 9
DAY_MS = 86_400_000
_cache = {}


def _ema(xs, n):
    k, out, e = 2.0 / (n + 1), [], None
    for x in xs:
        e = x if e is None else e + k * (x - e)
        out.append(e)
    return out


def _hist(closes):
    macd = [a - b for a, b in zip(_ema(closes, FAST), _ema(closes, SLOW))]
    return [m - s for m, s in zip(macd, _ema(macd, SIG))]


def _series(ctx, sym):
    k = (id(ctx), sym)
    if k not in _cache:
        kl = ctx.klines(sym)
        _cache[k] = ([r[0] for r in kl], [r[4] for r in kl], _hist([r[4] for r in kl]))
    return _cache[k]


def _quantile(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q / 100.0 * len(xs)))]


def _drops(h, lo, hi):
    """Падения от бегущего пика гистограммы на [lo, hi) индексов."""
    peak, out = None, []
    for v in h[lo:hi]:
        peak = v if peak is None or v > peak else peak
        out.append(peak - v)
    return out


def run(data, ctx, q):
    out, thr = {}, {}
    for r in data:
        sym, t0, ex = r["symbol"], int(r["t0_ns"]) // 1_000_000, int(r["exit_ns"]) // 1_000_000
        ts, cl, h = _series(ctx, sym)
        day0 = t0 - t0 % DAY_MS
        tk = (sym, day0, q)
        if tk not in thr:
            lo, hi = bisect.bisect_left(ts, day0 - DAY_MS), bisect.bisect_left(ts, day0)
            d = _drops(h, lo, hi)
            thr[tk] = _quantile(d, q) if d else None
        if thr[tk] is None:
            continue
        i = bisect.bisect_right(ts, t0)          # первая минута, открывшаяся после t0
        peak = None
        while i < len(ts) and ts[i] + 60_000 <= ex:
            peak = h[i] if peak is None or h[i] > peak else peak
            if peak - h[i] >= thr[tk] and peak - h[i] > 0:
                out[(r["symbol"], r["day_utc"], r["form"], r["signal_index"])] = ((ts[i] + 60_000) * 1_000_000, cl[i], True, "macd_trail")
                break
            i += 1
    return out
