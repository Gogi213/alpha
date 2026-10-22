"""П-07 H9 пауза по монете (Г-103): после закрытия круга монета не принимает новых сигналов pause минут. Плагин KIND='keep' (М5, В-227 пакет F),
порт p07-h9h10.py:simulate_pause (archive): тот же автомат «занято» busy-replay (day_idle/stopped по суткам монеты из step/idle_ns/residual
signals.csv) + персистентный по монете pause_until = exit_ns круга принятого сигнала + пауза. Круг принятого сигнала — из базы B1 месяца
(rounds.csv, поле round_exit_ns): круги, которых база не открывала, паузу не двигают. Любое закрытие (вариант «только стоп» — Г-103 в спеке
не требует). Месяцы считаются отдельно: пауза не переходит через границу месяца (хвост ≤ 8 ч, пренебрежимо). Домен — спека: {1,5,15,30,60,120,240,480} мин."""
NAME = "p07_h9_pause"
KIND, SOURCES = "keep", ()
PARAMS = {"pause": (1, 5, 15, 30, 60, 120, 240, 480)}


def run(data, ctx, pause):
    pause_ns = pause * 60 * 1_000_000_000
    by_sym = {}
    for d in data:
        by_sym.setdefault(d["symbol"], []).append(d)
    keep = []
    for rows in by_sym.values():
        rows.sort(key=lambda r: int(r["t0_ns"]))
        day_idle, stopped, cur_day, pause_until = None, False, None, -1
        for r in rows:
            if r["day_utc"] != cur_day:
                cur_day, day_idle, stopped = r["day_utc"], None, False
            t0 = int(r["t0_ns"])
            if stopped or (day_idle is not None and t0 < day_idle) or t0 < pause_until:
                continue
            keep.append((r["symbol"], r["day_utc"], r["form"], r["signal_index"]))
            if r["step"] in ("end_of_data", "no_window") or r["residual"] == "ended":
                stopped = True
            else:
                day_idle = int(r["idle_ns"])
            if r["round_exit_ns"]:
                pause_until = max(pause_until, int(r["round_exit_ns"]) + pause_ns)
    return keep
