"""TK-500: oi5m, liq5m, Φ_t эпизодов ядра из mark/funding/OI/ликвидаций двух ног -> <out>.aux.csv.
Запуск: aux_feats.py <out ядра> <корень A> <символ A> <корень B> <символ B>   (корень = каталог с <ЧЧ>/<символ>_*.parquet за сутки; чанк из нескольких суток — корни через запятую по порядку;
корень «-» — у ноги нет рядов mark/OI/ликвидаций (Bybit ob200, Gate: на боксе только стакан): ряд пуст, srcX = 0; oi5m — по ногам с источником, liq5m — только их ликвидации, Φ_t = NaN (ставка одной ноги неизвестна))
Ряды малы (секунды/минуты), поэтому pyarrow здесь не узкое место; горячий путь (книга, очередь) в Rust.
oi5m — среднее по ногам (v(t−)−v(t−5 мин))/v(t−5 мин), значения «строго до t»; liq5m — 1, если у любой ноги была ликвидация в [t−5 мин, t);
phi_bps — Φ_t: ns·(rA−rB)·1e4, ns = 1, если у любой ноги расчёт фандинга в (t, t+H], H=600 с (П-40 §3.5); знак Φ — в assemble.py."""
import csv, sys
import numpy as np
import pyarrow.parquet as pq
import pyarrow.compute as pc

H_MS, W5_MS = 600_000, 300_000


def f64(t, name):
    return pc.cast(t[name], "float64").to_numpy(zero_copy_only=False)


def series(roots, sym):
    if roots == "-":
        e, z = np.zeros(0, dtype=np.int64), np.zeros(0)
        return dict(ft=e, fv=z, fn=z, ot=e, ov=z, lq=e, src=0)
    fr_t, fr_v, fr_n, oi_t, oi_v, lq_t = [], [], [], [], [], []
    for h, root in ((h, r) for r in roots.split(",") for h in range(24)):
        d = f"{root}/{h:02d}/{sym}"
        m = pq.read_table(f"{d}_mark_price.parquet", columns=["event_time", "funding_rate", "next_funding_time"])
        v = f64(m, "funding_rate"); k = ~np.isnan(v)
        fr_t.append(m["event_time"].to_numpy()[k]); fr_v.append(v[k]); fr_n.append(f64(m, "next_funding_time")[k])
        o = pq.read_table(f"{d}_open_interest.parquet", columns=["timestamp", "sum_open_interest_value"])
        oi_t.append(o["timestamp"].to_numpy()); oi_v.append(f64(o, "sum_open_interest_value"))
        try:  # часы без ликвидаций файла не имеют
            lq_t.append(pq.read_table(f"{d}_liquidations.parquet", columns=["event_time"])["event_time"].to_numpy())
        except FileNotFoundError:
            lq_t.append(np.zeros(0, dtype=np.int64))
    cat = np.concatenate
    ft, fv, fn = cat(fr_t), cat(fr_v), cat(fr_n)
    oi_t, oi_v = cat(oi_t), cat(oi_v)
    a, b = np.argsort(ft, kind="stable"), np.argsort(oi_t, kind="stable")
    return dict(ft=ft[a], fv=fv[a], fn=fn[a], ot=oi_t[b], ov=oi_v[b], lq=np.sort(cat(lq_t)), src=1)


def before(arr, t):
    """индекс последнего элемента строго до t (−1, если нет)"""
    return int(np.searchsorted(arr, t, side="left")) - 1


def oi_chg(s, t):
    j1, j0 = before(s["ot"], t), before(s["ot"], t - W5_MS)
    if j1 < 0 or j0 < 0 or j1 == j0:
        return np.nan
    return (s["ov"][j1] - s["ov"][j0]) / s["ov"][j0]


def rate(s, t):
    j = before(s["ft"], t)
    return float(s["fv"][j]) if j >= 0 else np.nan


def settles(s, t):
    j = before(s["ft"], t)
    if j < 0 or np.isnan(s["fn"][j]):
        return False
    return t < s["fn"][j] <= t + H_MS


def main():
    out, ra, sa, rb, sb = sys.argv[1:6]
    A, B = series(ra, sa), series(rb, sb)
    rows = list(csv.DictReader(open(out + ".features.csv")))
    w = csv.writer(open(out + ".aux.csv", "w", newline=""))
    w.writerow(["theta", "t_start", "oi5m", "liq5m", "phi_bps", "n_s", "rA", "rB", "srcA", "srcB"])
    for r in rows:
        t = int(float(r["t_start"]))
        with np.errstate(all="ignore"):
            oi = np.nanmean([oi_chg(A, t), oi_chg(B, t)]) if not (np.isnan(oi_chg(A, t)) and np.isnan(oi_chg(B, t))) else np.nan
        liq = int(sum(int(((s["lq"] >= t - W5_MS) & (s["lq"] < t)).sum()) for s in (A, B)) > 0)
        fa, fb = rate(A, t), rate(B, t)
        ns = int(settles(A, t) or settles(B, t))
        phi = ns * (fa - fb) * 1e4 if not (np.isnan(fa) or np.isnan(fb)) else np.nan
        w.writerow([r.get("theta", ""), r["t_start"], float(oi), liq, float(phi), ns, float(fa), float(fb), A["src"], B["src"]])
    print("строк", len(rows), "->", out + ".aux.csv")


main()
