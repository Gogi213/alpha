"""Г-126: портфельный тормоз по числу монет у своих стен прямо сейчас. Плагин KIND='keep' (М5, В-227 пакет F).
Подход = сигнал B1 (signals.csv, любая монета, в том числе позже отброшенный занятостью — это подход, не позиция). Причинно: сигнал
пропускается, если число разных монет с сигналом в [t0 - window, t0] (включая свою) >= N. Окно — секунды; счёт по сигналам месяца
целиком (на стыке месяцев окно теряет хвост — пренебрежимо). Домены — спека Г-126: N {2,3,4,5,6,8,10} × окно {10,30,60 с}."""
import bisect

NAME = "g126"
KIND, SOURCES = "keep", ()
PARAMS = {"N": (2, 3, 4, 5, 6, 8, 10), "window": (10, 30, 60)}


def run(data, ctx, N, window):
    ev = sorted((int(d["t0_ns"]), d["symbol"]) for d in data)
    ts = [t for t, _ in ev]
    w_ns = window * 1_000_000_000
    keep = []
    for d in data:
        t0 = int(d["t0_ns"])
        lo, hi = bisect.bisect_left(ts, t0 - w_ns), bisect.bisect_right(ts, t0)
        if len({s for _, s in ev[lo:hi]} | {d["symbol"]}) < N:
            keep.append((d["symbol"], d["day_utc"], d["form"], d["signal_index"]))
    return keep
