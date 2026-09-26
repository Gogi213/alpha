"""Судья, П-02 R2 п.3(в) (2026-09-27): пересчёт по монетам из data/p02/p02-coin-counts-2026-09-27.json.
Сумма по монетам = суточный счётчик блока A; эффект без монеты с наибольшим влиянием; внутри монеты (МХ по монете,
веса — гармоническое n); стандартизация Г-08 по возрасту/размеру — из страт p02-wall-counts."""
import json
from collections import defaultdict

C = json.load(open("data/p02/p02-coin-counts-2026-09-27.json", encoding="utf-8"))["counts"]
VARS = {"g28_main": ("top", "bottom"), "g08_main": ("high", "low"), "g46_mismatch60": ("mismatch", "match")}
DAYFILES = {"g28_main": "p02-wall-counts-2026-09-26.json", "g08_main": "p02-wall-counts-2026-09-26.json",
            "g46_mismatch60": "p02-wave3-counts-2026-09-26.json"}


def eff(t):
    return (t[0] / t[1] - t[2] / t[3]) * 100 if t[1] and t[3] else None


for var, (up, base) in VARS.items():
    dc = json.load(open("data/p02/" + DAYFILES[var], encoding="utf-8"))["day_counts"]
    for pref in ("2026-08", "2026-09"):
        by_coin = defaultdict(lambda: [0, 0, 0, 0])
        mism = 0
        for day, coins in C.items():
            if not day.startswith(pref):
                continue
            s = [0, 0, 0, 0]
            for sym, v in coins.items():
                if var not in v:
                    continue
                u = v[var]
                z = [0, 0]
                t = [*u.get(up, z), *u.get(base, z)]
                for i in range(4):
                    by_coin[sym][i] += t[i]
                    s[i] += t[i]
            dv = dc.get(day, {}).get("variants", dc.get(day, {})).get(var)
            if dv is not None:
                ref = [dv[up][0], dv[up][1], dv[base][0], dv[base][1]]
                mism += ref != s
        tot = [sum(v[i] for v in by_coin.values()) for i in range(4)]
        e = eff(tot)
        infl = {k: e - (eff([tot[i] - v[i] for i in range(4)]) or 0) for k, v in by_coin.items()}
        top = max(infl, key=lambda k: infl[k] * (1 if e > 0 else -1))
        wo = eff([tot[i] - by_coin[top][i] for i in range(4)])
        num = den = 0.0
        for v in by_coin.values():
            if v[1] and v[3]:
                w = v[1] * v[3] / (v[1] + v[3])
                num += w * (v[0] / v[1] - v[2] / v[3]); den += w
        print(f"{var} {pref}: эффект {e:+.2f}, без {top} {wo:+.2f}, внутри монеты {num / den * 100:+.2f}, "
              f"сутки с расхождением сумм {mism}")

# Г-08: стандартизация по возрасту и по размеру (веса — доля касаний «high» в страте)
dc = json.load(open("data/p02/p02-wall-counts-2026-09-26.json", encoding="utf-8"))["day_counts"]
for axis in ("age", "size"):
    for pref in ("2026-08", "2026-09"):
        st = {}
        for lvl in ("lo", "mid", "hi"):
            var = f"g08_strat_{axis}_{lvl}"
            t = [0, 0, 0, 0]
            for day, x in dc.items():
                if day.startswith(pref) and var in x.get("variants", x):
                    u = x.get("variants", x)[var]
                    t = [t[0] + u["high"][0], t[1] + u["high"][1], t[2] + u["low"][0], t[3] + u["low"][1]]
            st[lvl] = t
        w = sum(t[1] for t in st.values())
        std = sum(t[1] / w * eff(t) for t in st.values())
        print(f"g08 стандартизовано по {axis} {pref}: {std:+.2f}")

# интервал стандартизированной разности: круговой блочный бутстреп по суткам, веса страт фиксированы
import numpy as np
for axis in ("age", "size"):
    for pref, b in (("2026-08", 4), ("2026-09", 3)):
        days = sorted(d for d in dc if d.startswith(pref))
        X = np.array([[[*dc[d].get("variants", dc[d]).get(f"g08_strat_{axis}_{l}", {}).get("high", [0, 0]),
                        *dc[d].get("variants", dc[d]).get(f"g08_strat_{axis}_{l}", {}).get("low", [0, 0])]
                       for l in ("lo", "mid", "hi")] for d in days], float)
        S = X.sum(0); w = S[:, 1] / S[:, 1].sum()
        n = len(days); k = -(-n // b); rng = np.random.default_rng(20260926)
        idx = ((rng.integers(0, n, (20000, k))[:, :, None] + np.arange(b)) % n).reshape(20000, -1)[:, :n]
        B = X[idx].sum(1)
        with np.errstate(invalid="ignore", divide="ignore"):
            v = ((B[:, :, 0] / B[:, :, 1] - B[:, :, 2] / B[:, :, 3]) * w).sum(1) * 100
        v = v[~np.isnan(v)]
        lo, hi = np.percentile(v, [2.5, 97.5]); p = min(1, 2 * min((v <= 0).mean(), (v >= 0).mean()))
        print(f"g08 станд. {axis} {pref}: [{lo:+.2f}; {hi:+.2f}] p {p:.4f}")
