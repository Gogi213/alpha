"""Г-80: дальность выброса против глубины позади стены (описание без Холма, в m не входит; KIND report: rounds.csv месяца + сигналы + подходы D20 + 1м свечи монеты).

depth_behind_lots - из подхода D20 (side=bid, arm_ms == t0_ns//1e6, тот же price_tick; связь через signals.csv, в rounds.csv price_tick нет). Выброс = max неблагоприятный ход
лонга от entry_vwap до exit_ns по low минутных свечей: (entry_vwap - min low)/entry_vwap в bps (минута выхода включена - upper bound глубины хода). Круги делятся на q корзин равного числа
по depth_behind_lots; строка на корзину: диапазон глубины, n, среднее/медиана выброса, доля стопов. Круги без подхода или без свечей монеты исключены (числа в строке)."""
import bisect

NAME = "g80_depth_excursion"
KIND, SOURCES = "report", ("klines", "signals", "approaches")
PARAMS = {"q": (3, 5)}


def run(data, ctx, q):
    sig = {(s["symbol"], s["day_utc"], s["form"], s["signal_index"]): s for s in ctx.signals()}
    appr, rows, no_appr, no_kl = {}, [], 0, 0
    for r in data:
        s = sig.get((r["symbol"], r["day_utc"], r["form"], r["signal_index"]))
        kl = ctx.klines(r["symbol"])
        if not kl:
            no_kl += 1; continue
        key = (r["symbol"], r["day_utc"])
        if key not in appr:
            appr[key] = ctx.approaches(*key)
        wall = appr[key].get((int(r["t0_ns"]) // 1_000_000, s["price_tick"])) if s else None
        if wall is None or not wall["depth_behind_lots"]:
            no_appr += 1; continue
        ts = [k[0] for k in kl]
        a, b = bisect.bisect_right(ts, int(r["t0_ns"]) // 1_000_000) - 1, bisect.bisect_right(ts, int(r["exit_ns"]) // 1_000_000)
        lows = [k[3] for k in kl[max(a, 0):b]]
        if not lows:
            no_kl += 1; continue
        ev = float(r["entry_vwap"])
        rows.append((float(wall["depth_behind_lots"]), max(0.0, (ev - min(lows)) / ev * 1e4), r["reason"] == "stop"))
    rows.sort()
    out = []
    for i in range(q):
        part = rows[i * len(rows) // q:(i + 1) * len(rows) // q]
        if not part:
            continue
        ex = sorted(p[1] for p in part)
        out.append({"bin": f"{i + 1}/{q}", "n": len(part), "depth_min": part[0][0], "depth_max": part[-1][0],
                    "excursion_mean_bps": round(sum(ex) / len(ex), 3), "excursion_median_bps": round(ex[len(ex) // 2], 3),
                    "share_stop": round(sum(1 for p in part if p[2]) / len(part), 4), "no_approach": no_appr, "no_klines": no_kl})
    return out
