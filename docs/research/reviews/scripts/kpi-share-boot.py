"""Судья 27.09: достижимость правила П-07 «p90 до перехая ≤ 5 сут» в форме «доля часов t с ожиданием > 5 сут ≤ 0,10»
(t с полным горизонтом). Точка по месяцам и одновыборочный блочный бутстреп: сутки месяца — круговыми блоками по 7,
август' + сентябрь' склеиваются в один путь, доля считается заново. Печатает долю выборок, где правило выполнено."""
import importlib.util, math, random, sys

spec = importlib.util.spec_from_file_location("kn", "tools/compute/kpi-newhigh.py")
kn = importlib.util.module_from_spec(spec); spec.loader.exec_module(kn)
S = kn.load("data/kpi")
H, DAY = 120.0, 86_400_000
MON = {"aug": (kn.ms("2026-08-01"), 31), "sep": (kn.ms("2026-09-01"), 23)}


def days_of(closes):
    out = {pk: [[] for _ in range(n)] for pk, (s, n) in MON.items()}
    for t, p in closes:
        for pk, (s, n) in MON.items():
            d = (t - s) // DAY
            if 0 <= d < n:
                out[pk][d].append((t - s - d * DAY, p))
    return out


def share(path_days, n_aug):
    """path_days — список суток [(смещение в сутках, p)]; доля часов с ожиданием > H по месяцам."""
    ev, c, eq = [], 0.0, []
    for i, day in enumerate(path_days):
        for off, p in sorted(day):
            ev.append(i * DAY + off); c += p; eq.append(c)
    pm, m = [], 0.0
    for v in eq:
        m = max(m, v); pm.append(m)
    end = len(path_days) * DAY
    res = {"aug": [0, 0], "sep": [0, 0]}
    import bisect
    t, j0 = 0, 0
    while t + H * 3_600_000 <= end:
        i = bisect.bisect_right(ev, t)
        mx = max(pm[i - 1] if i else 0.0, 0.0)
        lim = t + H * 3_600_000
        ok = False
        for j in range(i, len(ev)):
            if ev[j] > lim:
                break
            if eq[j] > mx + 1e-9:
                ok = True; break
        pk = "aug" if t < n_aug * DAY else "sep"
        res[pk][0] += (not ok); res[pk][1] += 1
        t += 3_600_000
    return {pk: a / b for pk, (a, b) in res.items() if b}


def boot(days, rng, L=7):
    out = []
    for pk in ("aug", "sep"):
        d = days[pk]; n = len(d); s = []
        while len(s) < n:
            i = rng.randrange(n); s += [d[(i + k) % n] for k in range(L)]
        out += s[:n]
    return out


names = sys.argv[1:] or ["Г-85 вход от фронтрана", "BTC 4 ч: трейл 1/1"]
rng = random.Random(7)
for nm in names:
    days = days_of(S[nm][1]["augsep"])
    pt = share(days["aug"] + days["sep"], 31)
    B = 400; ok = {"aug": 0, "sep": 0}; bad = {"aug": 0, "sep": 0}
    for _ in range(B):
        r = share(boot(days, rng), 31)
        for pk in ok:
            ok[pk] += r[pk] <= 0.10; bad[pk] += r[pk] > 0.10
    print(f"{nm}: точка доля > 5 сут авг {pt['aug']:.2f} сен {pt['sep']:.2f}; бутстреп «≤ 0,10» авг {ok['aug'] / B:.2f} "
          f"сен {ok['sep'] / B:.2f}")
