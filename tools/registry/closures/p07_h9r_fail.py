"""П-07 H9р (Г-38 S1, Г-145 S3): запрет повторного входа после неудачи. Плагин KIND='keep' (М5, В-227 пакет F), решение Судьи 12.10 (комм. 11:40).
Один проход: автомат «занято» (day_idle/stopped, как p07_h9_pause) в keep-фильтре; исход знают только принятые сигналы. Неудача = reason=='stop'
И exit_px < entry_vwap (строго; безубыток/выше — не неудача, b275d84). Сигнал без связи со стеной (side=bid, arm_ms==t0_ns//1e6, price_tick) ничем не режется.
  s1: k_fail {1,2,3,4,5,6,8,10} — после k-й неудачи на стене (symbol, price_tick, birth_ms) её сигналы с t0 >= выхода k-й неудачи режутся до снятия
      стены (level_death в истории дома; нет — до конца данных). k_fail=1 = прежняя клетка s1.
  s3: h {1,2,4,6,8,12,24,48} ч — эпизод = минуты btc_ret_4h_bps <= -44.55, склейка разрывов <= h, хвост h; неудача монеты, чей вход внутри эпизода
      (+хвост), запрещает её сигналы с t0 >= выхода неудачи до конца эпизода + h (неудача по любой стене монеты; вне эпизода запрета нет)."""
import bisect

NAME = "p07_h9r_fail"
KIND, SOURCES = "keep", ("approaches", "regime")
S1_K, S3_H = (1, 2, 3, 4, 5, 6, 8, 10), (1, 2, 4, 6, 8, 12, 24, 48)
PARAMS = {"cell": tuple(f"s1k{k}" for k in S1_K) + tuple(f"s3h{h}" for h in S3_H)}   # s1k<k_fail> (Г-38), s3h<часы> (Г-145)
BTC_THRESH = -44.55


def episodes(minutes, h_ms):
    """Эпизоды (start, end) минут просадки BTC: серии подряд идущих минут <= порога, склейка при разрыве <= h_ms (логика t32-epcap.build_episodes)."""
    runs, cur = [], None
    for mt in sorted(minutes):
        v = minutes[mt]
        if v is not None and v <= BTC_THRESH:
            cur = [mt, mt] if cur is None else [cur[0], mt]
        elif cur is not None:
            runs.append(tuple(cur)); cur = None
    if cur is not None:
        runs.append(tuple(cur))
    out = []
    for s, e in runs:
        if out and s - out[-1][1] <= h_ms:
            out[-1] = (out[-1][0], e)
        else:
            out.append((s, e))
    return out


def is_fail(d):
    return d["round_exit_ns"] != "" and d["reason"] == "stop" and d["entry_vwap"] != "" and float(d["exit_px"]) < float(d["entry_vwap"])


def run(data, ctx, cell):
    setup, n = cell[:2], int(cell[3:])
    h_ms = n * 3_600_000
    eps = starts = None
    if setup == "s3":
        eps = episodes(ctx.regime_minutes(), h_ms); starts = [e[0] for e in eps]
    by_sym = {}
    for d in data:
        by_sym.setdefault(d["symbol"], []).append(d)
    keep = []
    for sym, rows in by_sym.items():
        rows.sort(key=lambda r: int(r["t0_ns"]))
        deaths = ctx.wall_deaths(sym) if setup == "s1" else None
        approaches, fails, bans = {}, {}, []       # fails: стена -> [выходы неудач, ns]; bans: s3 [(от_ns, до_ns)]
        day_idle, stopped, cur_day = None, False, None
        for r in rows:
            if r["day_utc"] != cur_day:
                cur_day, day_idle, stopped = r["day_utc"], None, False
            t0 = int(r["t0_ns"])
            if stopped or (day_idle is not None and t0 < day_idle):
                continue
            wall = None
            if setup == "s1":
                if r["day_utc"] not in approaches:
                    approaches[r["day_utc"]] = ctx.approaches(sym, r["day_utc"])
                row = approaches[r["day_utc"]].get((t0 // 1_000_000, r["price_tick"]))
                if row is not None:
                    wall = (r["price_tick"], row["birth_ms"])
                    death = deaths.get(wall)
                    if sum(x <= t0 for x in fails.get(wall, ())) >= n and (death is None or t0 < death * 1_000_000):
                        continue
            elif any(a <= t0 < b for a, b in bans):
                continue
            keep.append((r["symbol"], r["day_utc"], r["form"], r["signal_index"]))
            if r["step"] in ("end_of_data", "no_window") or r["residual"] == "ended":
                stopped = True
            else:
                day_idle = int(r["idle_ns"])
            if not is_fail(r):
                continue
            ex = int(r["round_exit_ns"])
            if setup == "s1":
                if wall is not None:
                    fails.setdefault(wall, []).append(ex)
            else:
                t_ms = t0 // 1_000_000
                i = bisect.bisect_right(starts, t_ms) - 1
                if i >= 0 and eps[i][0] <= t_ms <= eps[i][1] + h_ms:
                    bans.append((ex, (eps[i][1] + h_ms) * 1_000_000))
    return keep
