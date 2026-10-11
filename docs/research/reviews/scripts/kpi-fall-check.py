"""Судья 27.09: проверка стороны падения KPI (cc691cf, kpi-newhigh.drawdown_stats).
Чувствительность числа эпизодов и «перелоу» к порогам DD_MIN_USD / REBOUND_FRAC, цензура восстановления,
и почасовые меры спада (доля часов под водой глубже D, язва-индекс) для сравнения вариантов."""
import importlib.util, json, math, sys

spec = importlib.util.spec_from_file_location("kn", "tools/compute/archive/kpi-newhigh.py")
kn = importlib.util.module_from_spec(spec); spec.loader.exec_module(kn)
S = kn.load("data/kpi")
names = [n for n in sys.argv[1:]] or ["BTC 4 ч: трейл 1/1", "Г-85 вход от фронтрана"]

def hourly_dd(closes):
    ev = sorted(closes); s0, e0 = kn.ms("2026-08-01"), kn.ms("2026-09-24")
    out = {"aug": [], "sep": []}; i, c, pk = 0, 0.0, 0.0; t = s0
    while t < e0:
        while i < len(ev) and ev[i][0] <= t:
            c += ev[i][1]; pk = max(pk, c); i += 1
        out["aug" if t < kn.ms("2026-09-01") else "sep"].append(pk - c)
        t += kn.MS_H
    return out

for n in names:
    ser = S[n][1]["augsep"]
    print(f"== {n}: закрытий {len(ser)}")
    for dmin, rb in ((10, .2), (5, .2), (20, .2), (10, .1), (10, .3)):
        kn.DD_MIN_USD, kn.REBOUND_FRAC = dmin, rb
        d = kn.drawdown_stats(ser)
        print(f"  DD_MIN {dmin:>2} REB {rb}: " + " | ".join(
            f"{pk} n={d[pk]['n']} relows={d[pk]['relows']} max={d[pk]['relows_max']} open={d[pk]['open']} "
            f"fall_p90={d[pk]['fall_p90']} depth_max={d[pk]['depth_max'] and round(d[pk]['depth_max'])}" for pk in ("aug", "sep")))
    h = hourly_dd(ser)
    for pk, xs in h.items():
        N = len(xs)
        ui = math.sqrt(sum(x * x for x in xs) / N)
        print(f"  {pk}: часов {N}; доля под водой >$0 {sum(x > 1e-9 for x in xs)/N:.2f}, >$10 {sum(x > 10 for x in xs)/N:.2f}, "
              f">$25 {sum(x > 25 for x in xs)/N:.2f}; язва RMS ${ui:.1f}; макс ${max(xs):.0f}")
