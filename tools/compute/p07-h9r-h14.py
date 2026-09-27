#!/usr/bin/env python3
"""П-07 H9р/H14 (поправка 3, В-133 `af69456`; уточнение владельца 27.09: неудача H9р — ТОЛЬКО
стоп, без F2) поверх базы Г-85а/Г-85б (T-31 busy-replay), по образцу `p07-h9h10.py` (H9 «пауза»).
Определения — `docs/research/P-07-g85-titration.md` §13, «Поправка 3». КЛЕТКИ НЕ СЧИТАТЬ до «ок»
Судьи на определения — этот файл только готовит инструмент и тождество.

**H9р — запрет повторного входа после неудачи (F1 = выход по стопу `reason=='stop'` в rounds.csv
С ЦЕНОЙ ВЫХОДА НИЖЕ ЦЕНЫ ВХОДА ПО VWAP — решение владельца к поправке 3 через CEO `b275d84`: стоп на
уровне входа или выше — безубыток/сдвиг стопа, НЕ неудача) в том же сетапе** (3 клетки, F2 снят по
слову владельца 27.09):
  S1 — та же стена `(side=bid, price_tick, birth_ms)`: запрет до её СНЯТИЯ. «Снятие» = disarm_ms
       подхода с `disarm_reason=='level_death'` где-либо в истории этой стены (по всем суткам того
       же дома — аug/hist/rec отдельно, стены разных месяцев не путаются); если снятие не найдено в
       доступных данных — запрет действует до конца данных дома (стена не умерла на наших глазах).
  S2 — тот же уровень ± 1 тик (любая стена на price_tick-1..price_tick+1 этой монеты) — запрет до
       конца суток UTC сигнала-неудачи.
  S3 — та же монета в том же эпизоде просадки BTC (`btc_ret_4h_bps <= -44.55`, разрывы ≤ 30 мин
       склеены, хвост епизода +31 мин — то же построение, что `t32-epcap.py:build_episodes`,
       логика скопирована ниже, т.к. на деке файла нет вне git) — запрет до конца эпизода (+ тот же
       хвост 31 мин, которым эпизод меряют в t32-epcap).
Сигнал под запретом выбрасывается ДО busy-replay (keep-фильтр); монета остаётся свободной для
других сетапов/сигналов — занятость пересчитывается заново.

**H14 — номер подхода стены на входе** (`approach_index` 0-based из кэша подходов + 1 = номер),
6 клеток: «только первые K» (K=1,2,3, накопительно) и «с N-го и дальше» (N=2,3,4). Сигналы БЕЗ
связи со стеной (не найдена строка кэша с тем же `arm_ms`/`price_tick`) — НЕ режутся ни одним
правилом H14, остаются во всех клетках (как в §7 `retries-2026-09-27.md`).

**Связь сигнал → стена.** `signals.csv` несёт `price_tick`, `day_utc`, `t0_ns`; кэш подходов
`study/approaches/D20/<day_utc>/approaches-<SYM>.csv` несёт `arm_ms` (сверено: `arm_ms == t0_ns //
1_000_000` у связанных сигналов), `price_tick`, `birth_ms`, `approach_index`, `disarm_ms`,
`disarm_reason`. Точное совпадение `(side=='bid', arm_ms, price_tick)` в файле того же дня даёт
единственную строку — точнее `tmp-t32/retries_link2.py` (тот сопоставлял только по `arm_ms` без
`price_tick`, т.к. `main-trades.csv` его не нёс; здесь `price_tick` есть прямо в сигнале).

**Занятость (T-31).** Оба правила — фильтр СИГНАЛОВ до `busy-replay.py`. Тот сам восстанавливает
автомат «занято» (`idle_ns`/`step`/`residual` по порядку `signal_index`) над тем, что осталось
после фильтра (см. `bin/busy-replay.py:replay()` — выброшенный сигнал в цикл автомата вообще не
попадает). Но H9р должен знать ИСХОД (стоп/не стоп) уже принятых сигналов, чтобы включать запрет
вовремя — а исход есть только у сигналов, реально прошедших автомат «занято». Поэтому Python
воспроизводит тот же автомат (`day_idle`/`stopped` по суткам монеты, как `simulate_pause` в
`p07-h9h10.py`) в одном проходе с фильтром сетапа/номера — порядок строго по времени, выброшенный
сигнал не создаёт своей сделки и не двигает `day_idle`. Повторный проход `busy-replay.py` над уже
уменьшённым набором — идемпотентен (сверяется тождеством ниже), как и было в `p07-h9h10.py`.

**Тождество (обязательно, НЕ счёт клеток):** `--cells keep` (без единого исключения, тот же
двойной проход) должно совпасть с базой `pause0` (`data/p07/h9h10-a.json`): август 297 сделок /
+$109.17, сентябрь 101 / +$173.51, доли p90>5сут 0.421 / 0.148.

    python3 p07-h9r-h14.py --variant a --cells keep,s1,s2,s3,k1,k2,k3,n2,n3,n4
"""
import argparse
import bisect
import csv
import datetime as dt
import glob
import importlib.util
import json
import os

HOME = os.path.expanduser("~/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT_ROOT = os.path.join(HOME, "tmp-p07")
GAP_MS = 30 * 60_000          # склейка разрывов эпизода BTC (t32-epcap.py)
TAIL_MS = 31 * 60_000         # хвост эпизода — то же построение, что t32-epcap.py
BTC_THRESH = -44.55           # порог главного варианта (btc4h_max)

CELL_FIRST_K = {"k1": 1, "k2": 2, "k3": 3}
CELL_FROM_N = {"n2": 2, "n3": 3, "n4": 4}
CELL_SETUP = {"s1", "s2", "s3"}
ALL_CELLS = ["keep", "s1", "s2", "s3", "k1", "k2", "k3", "n2", "n3", "n4"]


def load_p07base():
    """Импорт p07-h9h10.py по пути (сосед в той же папке; на деке — tmp-p07/) — переиспользуем
    HOMES/base_dir_for/load_signals/write_keep/busy_replay_for/portfolio_sim_for/kpi_of/load_kn
    без копирования, чтобы база бралась байт-в-байт тем же кодом."""
    path = os.path.join(HERE, "p07-h9h10.py")
    spec = importlib.util.spec_from_file_location("p07base", path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def day_start_ms(day):
    return int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


# ---------- связь сигнал -> стена (кэш подходов D20), по (home, symbol): лениво, раз на пару ----------

_SYM_CACHE = {}  # (home, symbol) -> (rows_by_day: {day_utc: {(arm_ms,price_tick): row}}, death_idx: {(price_tick,birth_ms): disarm_ms})


def _load_sym_data(home, symbol):
    key = (home, symbol)
    if key in _SYM_CACHE:
        return _SYM_CACHE[key]
    rows_by_day = {}
    death_idx = {}
    for day_dir in sorted(glob.glob(os.path.join(home, "study/approaches/D20", "20*"))):
        day_utc = os.path.basename(day_dir)
        fp = os.path.join(day_dir, f"approaches-{symbol}.csv")
        if not os.path.exists(fp):
            continue
        day_rows = {}
        with open(fp, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                if row["side"] != "bid":
                    continue
                pt = int(row["price_tick"])
                arm = int(row["arm_ms"])
                birth = int(row["birth_ms"])
                day_rows[(arm, pt)] = row
                if row["disarm_reason"] == "level_death":
                    death_idx[(pt, birth)] = int(row["disarm_ms"])
        rows_by_day[day_utc] = day_rows
    _SYM_CACHE[key] = (rows_by_day, death_idx)
    return _SYM_CACHE[key]


def wall_for_signal(home, symbol, day_utc, t0_ns, price_tick):
    rows_by_day, _ = _load_sym_data(home, symbol)
    day_rows = rows_by_day.get(day_utc)
    if not day_rows:
        return None
    return day_rows.get((t0_ns // 1_000_000, int(price_tick)))


def wall_death_ms(home, symbol, price_tick, birth_ms):
    _, death_idx = _load_sym_data(home, symbol)
    return death_idx.get((int(price_tick), int(birth_ms)))


# ---------- цены круга (entry_px/exit_px из rounds.csv) — p07base несёт в r["round"] только
# (exit_ns, reason), без цен; неудача H9р определяется ценой, поэтому читаем rounds.csv второй раз
# тем же ключом (symbol, day_utc, form, signal_index) ----------


def load_round_prices(day_dir, set_name):
    fp = os.path.join(day_dir, set_name, "rounds.csv")
    idx = {}
    if not os.path.exists(fp):
        return idx
    with open(fp, newline="", encoding="utf-8") as fh:
        lines = fh.read().splitlines(keepends=True)
    body = [l for l in lines if not l.startswith("#")]
    if not body:
        return idx
    for row in csv.DictReader(body):
        key = (row["symbol"], row["day_utc"], row["form"], row["signal_index"])
        idx[key] = (float(row["entry_px"]), float(row["exit_px"]))
    return idx


def is_stop_failure(round_tuple, px):
    """Неудача H9р: reason == 'stop' И цена выхода ниже цены входа (VWAP). Безубыток/стоп на
    уровне входа или выше (сдвиг после трейла, стоп на уровне снятой стены) — НЕ неудача."""
    if round_tuple is None or px is None:
        return False
    _exit_ns, reason = round_tuple
    entry_px, exit_px = px
    return reason == "stop" and exit_px < entry_px


# ---------- эпизоды просадки BTC (логика t32-epcap.py:load_regime/build_episodes, скопирована — на
# деке файла t32-epcap.py вне git нет) ----------

_EPISODES_CACHE = {}  # home -> [(start_ms, end_ms), ...]


def _day_of_ms(t_ms):
    return dt.datetime.fromtimestamp(t_ms / 1000, dt.timezone.utc).strftime("%Y-%m-%d")


def _load_regime_dir(home):
    m = {}
    for f in sorted(glob.glob(os.path.join(home, "study/regime", "20*.csv"))):
        day = os.path.basename(f)[:-4]
        with open(f, newline="", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                mt = int(row["minute_ms"])
                if _day_of_ms(mt) != day:
                    continue  # файл несёт хвост предыдущих суток для разогрева окна 4ч
                v = row["btc_ret_4h_bps"]
                m[mt] = float(v) if v not in ("", None) else None
    return m


def _build_episodes(regime):
    mins = sorted(regime)
    runs = []
    cur = None
    for mt in mins:
        under = regime[mt] is not None and regime[mt] <= BTC_THRESH
        if under:
            cur = [mt, mt] if cur is None else [cur[0], mt]
        else:
            if cur is not None:
                runs.append(tuple(cur))
                cur = None
    if cur is not None:
        runs.append(tuple(cur))
    episodes = []
    for s, e in runs:
        if episodes and s - episodes[-1][1] <= GAP_MS:
            episodes[-1] = (episodes[-1][0], e)
        else:
            episodes.append((s, e))
    return episodes


def episodes_for(home):
    if home not in _EPISODES_CACHE:
        _EPISODES_CACHE[home] = _build_episodes(_load_regime_dir(home))
    return _EPISODES_CACHE[home]


def episode_containing(episodes, t_ms):
    """Эпизод, которому принадлежит момент t_ms (с хвостом +31 мин, как t32-epcap.py), или None."""
    starts = [e[0] for e in episodes]
    i = bisect.bisect_right(starts, t_ms) - 1
    if i >= 0 and episodes[i][0] <= t_ms <= episodes[i][1] + TAIL_MS:
        return episodes[i]
    return None


# ---------- основной проход: автомат «занято» (как simulate_pause) + фильтр сетапа/номера ----------


def simulate(p, variant, cell):
    """[(symbol, t0_ns, price_tick)] сигналов, принятых под правилом `cell`.
    cell == "keep" -> без единого исключения (тождество с базой)."""
    by_symbol = {}
    bd = p.base_dir_for(variant)
    for tag, home in p.HOMES.items():
        for d in sorted(glob.glob(os.path.join(home, "b5", bd, "20*"))):
            day_utc = os.path.basename(d)
            px_idx = load_round_prices(d, p.SET_)
            for r in p.load_signals(d):
                r["_home"] = home
                r["_day_utc"] = day_utc
                key = (r["symbol"], r["day_utc"], r["form"], r["signal_index"])
                r["_px"] = px_idx.get(key)
                by_symbol.setdefault(r["symbol"], []).append(r)

    first_k = CELL_FIRST_K.get(cell)
    from_n = CELL_FROM_N.get(cell)
    setup = cell if cell in CELL_SETUP else None

    keep = []
    for symbol, rows in by_symbol.items():
        rows.sort(key=lambda r: r["t0_ns"])
        day_idle = None
        stopped = False
        cur_day = None
        s1_bans = {}   # (price_tick, birth_ms) -> ban_until_ns
        s2_bans = {}   # price_tick -> ban_until_ns (сброс на смене суток)
        s3_ban_until = -1
        for r in rows:
            if r["day_utc"] != cur_day:
                cur_day = r["day_utc"]
                day_idle = None
                stopped = False
                s2_bans = {}
            if stopped:
                continue
            t0 = r["t0_ns"]
            if day_idle is not None and t0 < day_idle:
                continue

            need_wall = first_k is not None or from_n is not None or setup == "s1"
            wall = wall_for_signal(r["_home"], symbol, r["_day_utc"], t0, r["price_tick"]) if need_wall else None

            excluded = False
            if first_k is not None:
                if wall is not None and int(wall["approach_index"]) + 1 > first_k:
                    excluded = True
            elif from_n is not None:
                if wall is not None and int(wall["approach_index"]) + 1 < from_n:
                    excluded = True
            elif setup == "s1":
                if wall is not None:
                    bkey = (int(r["price_tick"]), int(wall["birth_ms"]))
                    if t0 < s1_bans.get(bkey, -1):
                        excluded = True
            elif setup == "s2":
                pt = int(r["price_tick"])
                if any(t0 < s2_bans.get(pt + dd, -1) for dd in (-1, 0, 1)):
                    excluded = True
            elif setup == "s3":
                if t0 < s3_ban_until:
                    excluded = True

            if excluded:
                continue

            keep.append((symbol, str(r["t0_ns"]), r["price_tick"]))
            if r["step"] in ("end_of_data", "no_window") or r["residual"] == "ended":
                stopped = True
            else:
                day_idle = r["idle_ns"]

            if setup is not None and is_stop_failure(r["round"], r.get("_px")):
                if setup == "s1" and wall is not None:
                    death_ms = wall_death_ms(r["_home"], symbol, int(r["price_tick"]), int(wall["birth_ms"]))
                    ban_until_ns = death_ms * 1_000_000 if death_ms is not None else 2 ** 62
                    s1_bans[(int(r["price_tick"]), int(wall["birth_ms"]))] = ban_until_ns
                elif setup == "s2":
                    day_end_ns = (day_start_ms(r["_day_utc"]) + 86_400_000) * 1_000_000
                    s2_bans[int(r["price_tick"])] = day_end_ns
                elif setup == "s3":
                    eps = episodes_for(r["_home"])
                    ep = episode_containing(eps, t0 // 1_000_000)
                    if ep is not None:
                        s3_ban_until = max(s3_ban_until, (ep[1] + TAIL_MS) * 1_000_000)

        # память: кэш подходов D20 по (home, symbol) читает ВСЮ историю символа (десятки суток,
        # ~13 ГБ на дом на августовской эпохе) — без выселения после каждого символа процесс
        # накапливает кэш по всем 76 монетам разом и падает по памяти (замерено 27.09, 11.3 ГБ
        # пик + 6.1 ГБ подкачки на сверке соединения). by_symbol.items() проходит каждый символ
        # ровно один раз, дальше к его записям в _SYM_CACHE возврата нет — выселяем сразу.
        for _key in [k for k in _SYM_CACHE if k[1] == symbol]:
            del _SYM_CACHE[_key]
    return keep


def run_cell(p, kn, variant, cell, results):
    keep = simulate(p, variant, cell)
    keep_path = os.path.join(OUT_ROOT, f"h9r-{variant}-{cell}", "keep.csv")
    p.write_keep(keep, keep_path)
    outs = p.busy_replay_for(variant, f"h9r-{cell}", keep_path)
    ps, co = p.portfolio_sim_for(variant, f"h9r-{cell}", outs, max_pos=0)
    results[cell] = p.kpi_of(kn, co, variant)
    print(f"{cell}: n_signals_kept={len(keep)} kpi={results[cell]}", flush=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--variant", required=True, choices=["a", "b"])
    ap.add_argument("--cells", required=True, help="через запятую из: " + ",".join(ALL_CELLS))
    a = ap.parse_args()
    cells = [c.strip() for c in a.cells.split(",") if c.strip()]
    for c in cells:
        if c not in ALL_CELLS:
            raise SystemExit(f"неизвестная клетка {c!r}, ожидались: {ALL_CELLS}")

    p = load_p07base()
    kn = p.load_kn()
    out_json = os.path.join(OUT_ROOT, f"h9r-h14-{a.variant}.json")
    results = json.load(open(out_json, encoding="utf-8")) if os.path.exists(out_json) else {}

    for cell in cells:
        run_cell(p, kn, a.variant, cell, results)

    with open(out_json, "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("DONE", out_json)


if __name__ == "__main__":
    main()
