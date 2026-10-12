"""П-07 H10 (Г-20): потолок одновременных позиций по всему пулу. Плагин KIND='keep' (М5, В-227 пакет F), решение Судьи 12.10 (комм. 11:40).
Сигнал выбрасывается, если в момент t0 открыто >= cap принятых позиций (интервал [t0, выход): выход в тот же ns освобождает); круг без исхода
(round_exit_ns пуст) позицию не занимает. Один проход по времени с автоматом «занято» монеты (day_idle/stopped, как p07_h9_pause); выброшенный сигнал
монету не занимает. «Первые N на эпизод» (firstN) в сетку не входит. Домен — спека: cap {1,2,3,4,5,8,12,20}."""
import heapq

NAME = "p07_h10_cap"
KIND, SOURCES = "keep", ()
PARAMS = {"cap": (1, 2, 3, 4, 5, 8, 12, 20)}


def run(data, ctx, cap):
    rows = sorted(data, key=lambda r: (int(r["t0_ns"]), r["symbol"], int(r["signal_index"])))
    state, open_exits, keep = {}, [], []   # state: symbol -> [cur_day, day_idle, stopped]
    for r in rows:
        st = state.setdefault(r["symbol"], [None, None, False])
        if r["day_utc"] != st[0]:
            st[:] = [r["day_utc"], None, False]
        t0 = int(r["t0_ns"])
        if st[2] or (st[1] is not None and t0 < st[1]):
            continue
        while open_exits and open_exits[0] <= t0:
            heapq.heappop(open_exits)
        if len(open_exits) >= cap:
            continue
        keep.append((r["symbol"], r["day_utc"], r["form"], r["signal_index"]))
        if r["step"] in ("end_of_data", "no_window") or r["residual"] == "ended":
            st[2] = True
        else:
            st[1] = int(r["idle_ns"])
        if r["round_exit_ns"]:
            heapq.heappush(open_exits, int(r["round_exit_ns"]))
    return keep
