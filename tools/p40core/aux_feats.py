"""TK-500: oi5m, liq5m, Φ_t эпизодов ядра из mark/funding/OI/ликвидаций двух ног -> <out>.aux.csv.
Запуск: aux_feats.py <out ядра> <корень A> <символ A> <корень B> <символ B>   (корень = каталог с <ЧЧ>/<символ>_*.parquet за сутки; чанк из нескольких суток — корни через запятую по порядку;
корень «-» — у ноги нет рядов mark/OI/ликвидаций (Bybit ob200, Gate: на боксе только стакан): ряд пуст, srcX = 0; oi5m — по ногам с источником, liq5m — только их ликвидации, Φ_t = NaN (ставка одной ноги неизвестна))
TK-510: час без файла mark_price / open_interest у поставщика (Bitget 25.09 ч09–13, Aster 17.09 ч00 OI) — пустой ряд, строка (биржа, контракт, дата, час, вид) в <out>.auxholes.csv; признаки в окне дыры — «нет значения»:
oi5m = NaN, если окно [t−5 мин, t) задевает дыру OI любой ноги с источником; phi_bps/rA/rB = NaN и n_s = −1, если t в часе без mark любой ноги; liq5m = −1, если окно задевает час, которого нет у поставщика целиком (нет mark, OI и ликвидаций).
Без дыр результат прежний байт в байт.
TK-512 (П-40 §9): у Aster на боксе нет *_open_interest.parquet вовсе — OI не читается и дырой не пишется (источник OI = 0, как у корня «-»); mark/funding/ликвидации Aster читаются как прежде. В aux.csv колонки srcOIA/srcOIB (11–12): 1, если у ноги есть ряд OI;
oi5m — по ногам с рядом OI (Aster × Binance/OKX/Bitget — по второй ноге), у пары без OI на обеих ногах — NaN; assemble --aux-strict берёт oi5m «нет значения» при srcOIA+srcOIB < 2. Колонки 1–10 без Aster прежние байт в байт.
Ряды малы (секунды/минуты), поэтому pyarrow здесь не узкое место; горячий путь (книга, очередь) в Rust.
oi5m — среднее по ногам (v(t−)−v(t−5 мин))/v(t−5 мин), значения «строго до t»; liq5m — 1, если у любой ноги была ликвидация в [t−5 мин, t);
phi_bps — Φ_t: ns·(rA−rB)·1e4, ns = 1, если у любой ноги расчёт фандинга в (t, t+H], H=600 с (П-40 §3.5); знак Φ — в assemble.py."""
import calendar, csv, os, sys, time
import numpy as np
import pyarrow.parquet as pq
import pyarrow.compute as pc

H_MS, W5_MS = 600_000, 300_000
NO_OI_EX = {"aster"}  # поставщик на боксе не даёт open_interest вовсе (TK-511): не дыра, а нет источника


def f64(t, name):
    return pc.cast(t[name], "float64").to_numpy(zero_copy_only=False)


def leg_id(root):
    """<…>/chft/<биржа>_futures/<ГГГГ-ММ-ДД> -> (биржа, дата, начало суток мс UTC)"""
    r = root.rstrip("/")
    day, ex = os.path.basename(r), os.path.basename(os.path.dirname(r))
    return ex.removesuffix("_futures"), day, calendar.timegm(time.strptime(day, "%Y-%m-%d")) * 1000


def read_hour(path, columns):
    """None, если файла часа нет у поставщика"""
    try:
        return pq.read_table(path, columns=columns)
    except FileNotFoundError:
        return None


def series(roots, sym):
    e, z = np.zeros(0, dtype=np.int64), np.zeros(0)
    if roots == "-":
        return dict(ft=e, fv=z, fn=z, ot=e, ov=z, lq=e, src=0, soi=0, h_mk=[], h_oi=[], h_lq=[], rec=[])
    fr_t, fr_v, fr_n, oi_t, oi_v, lq_t = [], [], [], [], [], []
    h_mk, h_oi, h_lq, rec = [], [], [], []
    soi = int(leg_id(roots.split(",")[0])[0] not in NO_OI_EX)
    for root in roots.split(","):
        ex, day, t0 = leg_id(root)
        for h in range(24):
            d, iv = f"{root}/{h:02d}/{sym}", (t0 + h * 3600000, t0 + (h + 1) * 3600000)
            m = read_hour(f"{d}_mark_price.parquet", ["event_time", "funding_rate", "next_funding_time"])
            if m is None:
                h_mk.append(iv); rec.append((ex, sym, day, h, "mark_price"))
            else:
                v = f64(m, "funding_rate"); k = ~np.isnan(v)
                fr_t.append(m["event_time"].to_numpy()[k]); fr_v.append(v[k]); fr_n.append(f64(m, "next_funding_time")[k])
            o = read_hour(f"{d}_open_interest.parquet", ["timestamp", "sum_open_interest_value"]) if soi else None
            if o is None:
                if soi:
                    h_oi.append(iv); rec.append((ex, sym, day, h, "open_interest"))
            else:
                oi_t.append(o["timestamp"].to_numpy()); oi_v.append(f64(o, "sum_open_interest_value"))
            q = read_hour(f"{d}_liquidations.parquet", ["event_time"])  # часы без ликвидаций файла не имеют
            lq_t.append(e if q is None else q["event_time"].to_numpy())
            if q is None and m is None and o is None:  # часа нет у поставщика целиком: «ликвидаций нет» неизвестно
                h_lq.append(iv); rec.append((ex, sym, day, h, "liquidations"))
    cat = lambda xs, dt: np.concatenate(xs) if xs else np.zeros(0, dtype=dt)
    ft, fv, fn = cat(fr_t, np.int64), cat(fr_v, np.float64), cat(fr_n, np.float64)
    oi_t, oi_v = cat(oi_t, np.int64), cat(oi_v, np.float64)
    a, b = np.argsort(ft, kind="stable"), np.argsort(oi_t, kind="stable")
    return dict(ft=ft[a], fv=fv[a], fn=fn[a], ot=oi_t[b], ov=oi_v[b], lq=np.sort(cat(lq_t, np.int64)), src=1, soi=soi, h_mk=h_mk, h_oi=h_oi, h_lq=h_lq, rec=rec)


def in_hole(iv, lo, hi):
    """пересекает ли [lo, hi) любую дыру iv (список [a, b) в мс)"""
    return any(a < hi and b > lo for a, b in iv)


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
    with open(out + ".auxholes.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["exchange", "contract", "date", "hour", "kind"])
        w.writerows(A["rec"] + B["rec"])
    rows = list(csv.DictReader(open(out + ".features.csv")))
    w = csv.writer(open(out + ".aux.csv", "w", newline=""))
    w.writerow(["theta", "t_start", "oi5m", "liq5m", "phi_bps", "n_s", "rA", "rB", "srcA", "srcB", "srcOIA", "srcOIB"])
    for r in rows:
        t = int(float(r["t_start"]))
        oa, ob = oi_chg(A, t), oi_chg(B, t)
        with np.errstate(all="ignore"):
            oi = np.nanmean([oa, ob]) if not (np.isnan(oa) and np.isnan(ob)) else np.nan
        if any(in_hole(s["h_oi"], t - W5_MS, t) for s in (A, B)):
            oi = np.nan
        liq = int(sum(int(((s["lq"] >= t - W5_MS) & (s["lq"] < t)).sum()) for s in (A, B)) > 0)
        if any(in_hole(s["h_lq"], t - W5_MS, t) for s in (A, B)):
            liq = -1
        fa, fb = rate(A, t), rate(B, t)
        ns = int(settles(A, t) or settles(B, t))
        phi = ns * (fa - fb) * 1e4 if not (np.isnan(fa) or np.isnan(fb)) else np.nan
        if any(in_hole(s["h_mk"], t, t + 1) for s in (A, B)):
            fa = fb = phi = np.nan
            ns = -1
        w.writerow([r.get("theta", ""), r["t_start"], float(oi), liq, float(phi), ns, float(fa), float(fb), A["src"], B["src"], A["soi"], B["soi"]])
    print("строк", len(rows), "->", out + ".aux.csv", "дыр", len(A["rec"]) + len(B["rec"]))


if __name__ == "__main__":
    main()
