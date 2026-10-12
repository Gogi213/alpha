"""Г-144: хедж по бете к BTC (описание без Холма, в m не входит; KIND report: rounds.csv месяца + 1м свечи монеты и BTCUSDT).

Бета монеты = наклон МНК 1м-доходностей монеты к BTC за bd ПРОШЛЫХ суток до суток круга (причинно; в пределах свечей месяца монеты, < 1000 пар - круг
без беты). Хедж = короткая (для лонга) позиция BTC на beta*номинал на окне [t0, exit]: доход хеджа = -dir*beta*r_btc(t0..exit) в bps, издержки = 2 ноги тейкера
|beta|*2*TAKER*(1-ребейт) (cost.rs). Строки: круги до/после хеджа (среднее и СКО net_bps на круг), число кругов без свечей монеты / без беты."""
import bisect
import math

NAME = "g144_beta_hedge"
KIND, SOURCES = "report", ("klines", "btc")
PARAMS = {"bd": (1, 3, 7)}
TAKER_BPS = 3.5 * 0.9
DAY_MS = 86_400_000
_cache = {}


def _rets(ts, cl, lo, hi):
    """{минута: лог-доходность} по соседним минутам индексов [lo, hi)."""
    return {ts[i]: math.log(cl[i] / cl[i - 1]) for i in range(max(lo, 1), hi) if ts[i] - ts[i - 1] == 60_000}


def _btc(ctx):
    k = ("btc", id(ctx))
    if k not in _cache:
        b = ctx.btc(); _cache[k] = ([x[0] for x in b], [x[1] for x in b])
    return _cache[k]


def _beta(ctx, sym, day0, bd):
    kl = ctx.klines(sym)
    ts, cl = [r[0] for r in kl], [r[4] for r in kl]
    lo, hi = bisect.bisect_left(ts, day0 - bd * DAY_MS), bisect.bisect_left(ts, day0)
    bt, bc = _btc(ctx)
    bi = {t: i for i, t in enumerate(bt)}
    xs, ys = [], []
    for t, r in _rets(ts, cl, lo, hi).items():
        i = bi.get(t)
        if i and bt[i - 1] == t - 60_000:
            xs.append(math.log(bc[i] / bc[i - 1])); ys.append(r)
    if len(xs) < 1000:
        return None
    mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
    var = sum((x - mx) ** 2 for x in xs)
    return sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / var if var > 0 else None


def _btc_ret_bps(ctx, a, b):
    bt, bc = _btc(ctx)
    i, j = bisect.bisect_right(bt, a) - 1, bisect.bisect_right(bt, b) - 1
    return (bc[j] / bc[i] - 1) * 1e4 if i >= 0 and j > i else None


def run(data, ctx, bd):
    before, after, betas, nok, nobeta = [], [], [], 0, 0
    cache = {}
    for r in data:
        if not ctx.klines(r["symbol"]):
            nok += 1; continue
        t0, ex = int(r["t0_ns"]) // 1_000_000, int(r["exit_ns"]) // 1_000_000
        key = (r["symbol"], t0 - t0 % DAY_MS)
        if key not in cache:
            cache[key] = _beta(ctx, key[0], key[1], bd)
        beta, rb = cache[key], _btc_ret_bps(ctx, t0, ex)
        if beta is None or rb is None:
            nobeta += 1; continue
        nb = float(r["net_bps"])
        before.append(nb); betas.append(beta)
        after.append(nb - int(r["dir"]) * beta * rb - abs(beta) * 2 * TAKER_BPS)

    def sd(xs):
        m = sum(xs) / len(xs)
        return math.sqrt(sum((x - m) ** 2 for x in xs) / len(xs))
    if not before:
        return [{"n": 0, "no_coin_klines": nok, "no_beta": nobeta}]
    n = len(before)
    return [{"n": n, "no_coin_klines": nok, "no_beta": nobeta, "beta_mean": round(sum(betas) / n, 4),
             "mean_bps_before": round(sum(before) / n, 4), "mean_bps_after": round(sum(after) / n, 4),
             "sd_bps_before": round(sd(before), 4), "sd_bps_after": round(sd(after), 4)}]
