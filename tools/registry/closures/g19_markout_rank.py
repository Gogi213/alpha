"""Г-19: токсичные заполнения - markout по рангу (описание без Холма, в m не входит; KIND report: rounds.csv месяца + 1м свечи монеты).

Ранг = legs_filled круга B1 (сколько ступеней лестницы ladder3 заполнилось, 1..3; в rounds.csv номера ступени нет). Markout(h) = dir*(close минуты t0+h / entry_vwap - 1)
в bps, h из PARAMS (мин). t0 - момент взвода входа: момента заливки rounds.csv не пишет (метка в отчёте); close последней закрытой минуты <= t0+h. Монеты без свечей исключены
(число в строке). Строка на (h, ранг) и «all»: n, среднее, медиана, доля markout<0."""
import bisect

NAME = "g19_markout_rank"
KIND, SOURCES = "report", ("klines",)
PARAMS = {"h": (1, 5, 15)}


def run(data, ctx, h):
    acc, nok = {}, 0
    for r in data:
        kl = ctx.klines(r["symbol"])
        if not kl:
            nok += 1; continue
        ts = [k[0] for k in kl]
        i = bisect.bisect_right(ts, int(r["t0_ns"]) // 1_000_000 + h * 60_000 - 60_000) - 1     # минута, закрытая к t0+h
        if i < 0 or ts[i] + 60_000 > int(r["t0_ns"]) // 1_000_000 + h * 60_000:
            continue
        m = int(r["dir"]) * (kl[i][4] / float(r["entry_vwap"]) - 1) * 1e4
        for k in (r["legs_filled"], "all"):
            acc.setdefault(k, []).append(m)
    out = []
    for k, xs in sorted(acc.items()):
        xs = sorted(xs)
        out.append({"rank": k, "n": len(xs), "no_klines_rounds": nok, "mean_bps": round(sum(xs) / len(xs), 3),
                    "median_bps": round(xs[len(xs) // 2], 3), "share_neg": round(sum(1 for x in xs if x < 0) / len(xs), 4)})
    return out
