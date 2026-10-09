#!/usr/bin/env python3
"""`_lib.portfolio` — правила портфеля счёта и «просадка счёта» (П1), перенесённые байт-в-байт из
`portfolio-sim.py` (T-21, `docs/findings/t21-metrics-canon-2026-09-27.md` §3 п.5, шаг после «`_lib`
модулем» — было заглушкой, теперь сам перенос). Формулы не менялись: тождественность проверена
локально гейтом `tools/compute/t21-psim-gate.py` (синтетика) и тестами `tools/compute/tests` — на
реальных прогонах Steam Deck сверяет автор задачи `--real`.

Перенесено (оригинал был `portfolio-sim.py`, ниже — прежние номера строк на `93cba5f`):
  - «одна позиция на монету» (`busy`/`open_syms`), потолок числа позиций, стоп дня, серия убытков
    подряд (E27), выключатель BTC / исключение монет, доплата тейкер-комиссии при принудительном
    закрытии, фандинг по факт. отметкам — всё в `simulate` (:319-429);
  - «просадка счёта» (П1, минутная переоценка открытых позиций) — `minute_curve` (:246-316);
  - «откат по закрытиям» (П2, старая модель «растёт только на закрытии») — `closed_drawdown`
    (:231-243). Это НЕ то же самое, что `_lib.metrics.drawdown_closed`: тот port держит формулу для
    канонической строки сделки (`Trade`/dict с `t1_ns`/`pnl_usd`), а этот — для собственного формата
    `taken` симуляции портфеля (`t1`/`pnl`, dict без `_ns`). `_lib/__init__.py` документирует
    `_lib.trade`/`_lib.epoch`/`_lib.metrics`/`_lib.portfolio` как самостоятельные файлы без взаимных
    импортов — приводить форматы друг к другу здесь означало бы это нарушить, поэтому `simulate`
    использует СВОЙ `closed_drawdown` (формулы совпадают дословно — так и было при переносе формулы
    в `metrics.py` 27.09, см. его докстрока), а не импортирует `_lib.metrics`. Оба места дают
    одинаковое число на одних и тех же сделках, только на разных представлениях строки;
  - чтение прогонов и режима BTC (`load_run`, `load_rounds`, `load_btc1h`), классы `Klines`/`Funding`,
    сводка «взятых» сделок (`taken_stats`), день сделки для денег (`day_of`, по `t1`);
  - константы: комиссия ноги (`MAKER_LEG_BPS`/`TAKER_LEG_BPS`, В-63), исходы-мейкер выхода
    (`MAKER_EXIT_REASONS`), единицы времени (`NS`/`MIN_MS`/`DAY_NS`), множитель размера позиции
    (`SIZE_MULT`, П-05 §3а — мутируемый модульный глобал, CLI `--size-mult` меняет его на время
    прогона).

`SIZE_MULT` — единственная тонкость переноса: `portfolio-sim.py` мутирует его как модульный глобал
(`global SIZE_MULT`, тест `test_size_mult_scales_usd_linearly` — тоже, прямым присваиванием атрибуту
модуля). Модуль `_lib.portfolio` — ДРУГОЙ объект модуля, поэтому присваивание `psim.SIZE_MULT = X`
само по себе не меняет `_lib.portfolio.SIZE_MULT`, который читает `load_run` этого файла;
`portfolio-sim.py` синхронизирует его тонким `load_run`-обёртчиком (см. там) — единственное место
логики не перенесённое дословно, остальное — без изменений.

`DROP_DEFAULT` — как было (В-105), не входит в исходный `portfolio-sim.py`, заведена здесь раньше."""
from __future__ import annotations

import bisect
import csv
import datetime as dt
import glob
import itertools
import os

DROP_DEFAULT = {"TRXUSDT"}  # В-105: TRX вне торгового пула

NS = 1_000_000_000
MIN_MS = 60_000
DAY_NS = 86_400 * NS

# В-63 (`src/lob/costs.rs::leg_fee_bps`) — комиссия ноги после возврата 10 %: мейкер 1,26 / тейкер 3,15 bps.
MAKER_LEG_BPS = 1.26
TAKER_LEG_BPS = 3.15
# исходы, у которых выход — лимитный мейкер (`ExitReason::{Take,Horizon}`, `strategy.rs`); всё остальное —
# уже рынок/тейкер, принудительное закрытие ничего не меняет в комиссии
MAKER_EXIT_REASONS = {"take", "horizon"}

# П-05 §3а (владелец 27.09: «меньше сделок, но больше объём за счёт плеча»): множитель размера сделки — $ позиции
# (`qty × entry_vwap`) × SIZE_MULT, линейно, **без пересчёта очереди** (исполнение и цена — как у прогона). 1.0 — как было.
SIZE_MULT = 1.0


def taken_stats(taken):
    """Сделки, которые счёт реально взял (после «одна позиция на монету», потолка, стопа дня, выключателя):
    средняя в $ и bps, средние плюс/минус, профит-фактор (по bps, как `titration-dashboard-merge.trade_stats`),
    минуты в рынке (объединение интервалов). T-20 п.4 (26.09): плитки дашборда брали их из всех строк прогона."""
    n = len(taken)
    if not n:
        return {"avg_usd": 0.0, "avg_bps": 0.0, "avg_win_bps": 0.0, "avg_loss_bps": 0.0,
                "profit_factor": None, "open_minutes": 0.0}
    wins = [x["net"] for x in taken if x["net"] > 0]
    losses = [x["net"] for x in taken if x["net"] <= 0]
    gl = -sum(losses)
    open_ns, cur_a, cur_b = 0, None, None
    for a, b in sorted((x["t0"], x["t1"]) for x in taken):
        if cur_b is None or a > cur_b:
            if cur_b is not None:
                open_ns += cur_b - cur_a
            cur_a, cur_b = a, b
        else:
            cur_b = max(cur_b, b)
    open_ns += cur_b - cur_a
    return {
        "avg_usd": sum(x["pnl"] for x in taken) / n,
        "avg_bps": sum(x["net"] for x in taken) / n,
        "avg_win_bps": sum(wins) / len(wins) if wins else 0.0,
        "avg_loss_bps": sum(losses) / len(losses) if losses else 0.0,
        "profit_factor": (sum(wins) / gl) if gl > 0 else None,
        "open_minutes": open_ns / 60e9,
    }


def one_per_coin(rows):
    """Одна позиция на монету — подбор входов «как торгует бот» ДО полного `simulate` (используют
    E28/E29/E30/E31 через `exit-sim.py`/`family-titrate.py` и `loss-days.py`; T-21 batch 1, было
    тройной копией `busy[sym]`/`busy_until[sym]` — во всех трёх байт-в-байт одно правило, сверено):
    вход монеты пропускается, пока не закрылась предыдущая (строго `>` — совпадение `t1 == t0`
    новый вход не блокирует, как и `simulate`'s `open_syms`, но здесь без учёта потолка/стопа дня —
    это лишь дедупликация перекрывающихся входов той же монеты). Сортирует `rows` по `t0` (как было
    на месте каждого вызова) — результат в этом порядке."""
    taken, busy = [], {}
    for r in sorted(rows, key=lambda x: x["t0"]):
        if busy.get(r["sym"], 0) > r["t0"]:
            continue
        busy[r["sym"]] = r["t1"]
        taken.append(r)
    return taken


def load_run(home, run, set_name, form):
    rows = []
    for f in sorted(glob.glob(os.path.join(home, run, "20*", set_name, "rounds.csv"))):
        with open(f, encoding="utf-8") as fh:
            lines = fh.readlines()
        # T-31 (условие Судьи 2): сырой прогон `--busy-skip off` — все сигналы без правила «занято» движка;
        # считать его можно только после `busy-replay.py` (метка `# busy_replay=`)
        head = [l for l in lines if l.startswith("#")]
        if any("busy_skip=off" in l for l in head) and not any(l.startswith("# busy_replay=") for l in head):
            raise SystemExit(f"{f}: прогон --busy-skip off без busy-replay.py — занятость движка не применена")
        for r in csv.DictReader(line for line in lines if not line.startswith("#")):
            if r["form"] != form:
                continue
            entry = float(r.get("entry_vwap") or 0) or float(r["entry_px"])
            gross = (float(r["exit_px"]) / entry - 1) * 1e4 * int(r["dir"])
            net = float(r["net_bps"])
            rows.append({"t0": int(r["t0_ns"]), "t1": int(r["exit_ns"]), "sym": r["symbol"], "net": net,
                         "reason": r["reason"], "entry": entry, "fee": gross - net, "dir": int(r["dir"]),
                         "usd": float(r["qty"]) * entry * SIZE_MULT, "fill": float(r.get("fill_frac") or 1.0)})
    return rows


def load_rounds(home, runs, set_name, form):
    """Сделки формы из первого прогона (список через запятую), где она есть."""
    for run in runs.split(","):
        rows = load_run(home, run, set_name, form)
        if rows:
            return rows
    return []


def load_btc1h(home):
    """Минуты (мс) и ход BTC за 1 ч, bps — из study/regime/<сутки>.csv (ночь, regime.py); из каждого
    файла — только минуты его собственных суток (файл может нести и соседние, как titration-points.py),
    без склейки через set() — окна суток по построению не пересекаются."""
    pairs = []
    for f in sorted(glob.glob(os.path.join(home, "study", "regime", "20??-??-??.csv"))):
        day = os.path.basename(f)[:-4]
        start = int(dt.datetime.strptime(day, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp() * 1000)
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if not r.get("btc_ret_1h_bps"):
                    continue
                m = int(r["minute_ms"])
                if start <= m < start + 86_400_000:
                    pairs.append((m, float(r["btc_ret_1h_bps"])))
    pairs.sort()
    return [m for m, _ in pairs], [v for _, v in pairs]


class Klines:
    """Закрытия минутных свечей монет (`ref-<SYM>-1m.csv`, минуты всех каталогов вместе)."""

    def __init__(self, dirs):
        self.dirs, self.cache, self.keys = dirs, {}, {}

    def _load(self, sym):
        if sym not in self.cache:
            data = {}
            for d in self.dirs:
                p = os.path.join(d, f"ref-{sym}-1m.csv")
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as fh:
                        data.update((int(r["minute_ms"]), float(r["close"])) for r in csv.DictReader(fh))
            self.cache[sym] = data
            self.keys[sym] = sorted(data)
        return self.cache[sym]

    def close(self, sym, minute_ms):
        return self._load(sym).get(minute_ms)

    def covered(self, sym):
        """Есть ли у монеты хоть одна свеча (в любом из каталогов) — иначе mark-to-market по ней
        не считается, только по закрытиям (n_no_klines)."""
        return bool(self._load(sym))

    def last_close(self, sym, minute_ms, since_ms=None):
        """Цена на эту минуту, а если свечи на неё нет — последняя известная не позже неё (None —
        свечей вообще не было до этой минуты). `since_ms` — не брать свечу старше этой минуты (вход
        сделки): 24.09 у RAYDIUMUSDT свечей за сентябрь 2026 не было, и «последней известной» стала
        свеча обвала октября 2025 из соседнего каталога — $1,87 при входе $0,84, мнимые +$460 на
        позиции и ложная просадка счёта −15,8 % вместо −2 %. Старше входа — не знаем (None)."""
        d = self._load(sym)
        if minute_ms in d:
            return d[minute_ms]
        ks = self.keys[sym]
        i = bisect.bisect_right(ks, minute_ms) - 1
        if i < 0 or (since_ms is not None and ks[i] < since_ms):
            return None
        return d[ks[i]]


class Funding:
    """Фандинг по монете (В-135 п.3): отметки `funding_time_ms,funding_rate` (ставка — доля номинала,
    «+» лонг платит, «−» лонг получает), интервалы у монет разные — берутся фактические отметки из CSV.
    По умолчанию (path=None) фандинг выключен — старые прогоны воспроизводимы без изменений."""

    def __init__(self, path):
        self.marks = {}
        if not path:
            return
        with open(path, encoding="utf-8") as f:
            for r in csv.DictReader(f):
                self.marks.setdefault(r["symbol"], []).append((int(r["funding_time_ms"]), float(r["funding_rate"])))
        for sym, ms in self.marks.items():
            ms.sort()

    def cost_usd(self, sym, t0_ms, t1_ms, direction, usd):
        """Издержка круга = Σ ставок отметок в (вход, выход] × номинал (знак — как у комиссии, вычитается
        из pnl): лонг платит положительную ставку (cost>0), шорт — получает (cost<0)."""
        ms = self.marks.get(sym)
        if not ms:
            return 0.0
        times = [t for t, _ in ms]
        lo, hi = bisect.bisect_right(times, t0_ms), bisect.bisect_right(times, t1_ms)
        if hi <= lo:
            return 0.0
        return direction * sum(rate for _, rate in ms[lo:hi]) * usd


def day_of(t_ns):
    return dt.datetime.fromtimestamp(t_ns / NS, dt.timezone.utc).strftime("%Y-%m-%d")


def closed_drawdown(taken, deposit):
    """Просадка по капиталу прежней модели (П2, «откат по закрытиям»): растёт только на закрытии
    сделки (шаг по `t1`), без переоценки открытых позиций между сделками. Формат `taken` — свой
    (`t1`/`pnl`), см. докстроку модуля про `_lib.metrics.drawdown_closed`."""
    eq = peak = deposit
    max_dd = max_dd_pct = 0.0
    for x in sorted(taken, key=lambda x: x["t1"]):
        eq += x["pnl"]
        if eq >= peak:
            peak = eq
        else:
            max_dd = max(max_dd, peak - eq)
            max_dd_pct = max(max_dd_pct, (peak - eq) / peak * 100 if peak else 0.0)
    return max_dd, max_dd_pct


def minute_curve(taken, klines, deposit, total):
    """Просадка и восстановление по минутной переоценке (mark-to-market) открытых позиций (П1,
    «просадка счёта»): капитал между сделками движется по закрытию минутной свечи монеты, направление
    и издержки круга — как у самой сделки. Нет свечи на минуту — берём последнюю известную
    (`Klines.last_close`); монета без свечей вовсе остаётся на модели «по закрытиям» (её берёт
    n_no_klines).

    События группируются по общей метке времени `t` (`itertools.groupby` по отсортированному списку):
    при ≥ 2 одновременно открытых позициях (норма — пик 8–11, SETTLED В-90) переоценки и
    закрытия РАЗНЫХ монет на одну и ту же минуту применяются к `active`/`realized_total` все разом,
    и только потом считается `eq` и сверяется с `peak`/`max_dd` — один раз на метку времени, а не
    по-событийно. По-событийный расчёт (старая версия) видел фиктивные промежуточные состояния
    капитала между обновлением первой и второй монеты той же группы — искажение только в сторону
    завышения просадки (см. test_minute_curve_groups_same_minute_events_before_drawdown)."""
    no_kline_syms = {x["sym"] for x in taken if not klines.covered(x["sym"])}
    events = []  # (t_ns, kind, idx, minute_ms) kind 0=переоценка (раньше при равенстве), 1=закрытие
    entry_min = {}
    for idx, x in enumerate(taken):
        events.append((x["t1"], 1, idx, None))
        if x["sym"] in no_kline_syms:
            continue
        m = (x["t0"] // 1_000_000 // MIN_MS) * MIN_MS
        entry_min[idx] = m
        tc = (m + MIN_MS) * 1_000_000
        while tc < x["t1"]:
            events.append((tc, 0, idx, m))
            m += MIN_MS
            tc = (m + MIN_MS) * 1_000_000
    events.sort()

    active, realized_total = {}, 0.0
    peak = deposit
    peak_t = None
    max_dd = max_dd_pct = 0.0
    open_since = None
    longest = 0.0
    last_t = None
    for t, group in itertools.groupby(events, key=lambda e: e[0]):
        for _, kind, idx, m in group:
            x = taken[idx]
            if kind == 0:
                # Свеча не старше минуты входа: «последняя известная» из чужой эпохи (другой
                # каталог свечей — год назад) давала мнимую переоценку (24.09, RAYDIUMUSDT).
                px = klines.last_close(x["sym"], m, since_ms=entry_min.get(idx))
                if px is None:
                    px = x["entry"]
                net_bps = (px / x["entry"] - 1) * 1e4 * x["dir"] - x["fee"]
                active[idx] = net_bps / 1e4 * x["usd"]
            else:
                active.pop(idx, None)
                realized_total += x["pnl"]
        eq = deposit + realized_total + sum(active.values())
        if eq >= peak:
            if open_since is not None:
                longest = max(longest, (t - open_since) / DAY_NS)
                open_since = None
            peak, peak_t = eq, t
        else:
            if open_since is None:
                open_since = peak_t if peak_t is not None else t
            max_dd = max(max_dd, peak - eq)
            max_dd_pct = max(max_dd_pct, (peak - eq) / peak * 100 if peak else 0.0)
        last_t = t
    unrecovered = open_since is not None
    if unrecovered and last_t is not None:
        longest = max(longest, (last_t - open_since) / DAY_NS)
    return {
        "dd_usd": max_dd, "dd_pct": max_dd_pct,
        "rf": (total / max_dd) if max_dd > 0 else None,
        "rec_days": longest, "unrecovered": unrecovered,
        "n_no_klines": len(no_kline_syms),
    }


def simulate(rows, btc, klines, deposit, max_pos, day_stop_pct, kill_bps, exclude, gap_pct, streak_stop=0,
             funding=None):
    minutes, vals = btc
    kills = [m for m, v in zip(minutes, vals) if v <= -kill_bps] if kill_bps else []
    open_pos = []  # (t1, pnl_usd, usd, sym)
    open_syms = set()
    realized = {}
    skipped = {"позиций": 0, "день": 0, "btc": 0, "монета": 0, "занята": 0, "серия": 0}
    streak = {}  # монета → (месяц, убыточных подряд) — по закрытым своим сделкам
    stopped = {}  # монета → месяц, до конца которого она выключена серией
    killed = no_kline = 0
    taken = []
    peak_n = 0
    peak_usd = 0.0

    def settle(upto):
        nonlocal open_pos
        keep = []
        for t1, p, u, sym in open_pos:
            if t1 <= upto:
                d = day_of(t1)
                realized[d] = realized.get(d, 0.0) + p
                open_syms.discard(sym)
                if streak_stop:
                    month, n_loss = streak.get(sym, (d[:7], 0))
                    n_loss = (n_loss if month == d[:7] else 0) + 1 if p <= 0 else 0
                    streak[sym] = (d[:7], n_loss)
                    if n_loss >= streak_stop:
                        stopped[sym] = d[:7]
            else:
                keep.append((t1, p, u, sym))
        open_pos = keep

    for r in sorted(rows, key=lambda x: x["t0"]):
        t0, t1, net, reason, fee = r["t0"], r["t1"], r["net"], r["reason"], r["fee"]
        settle(t0)
        if r["sym"] in exclude:
            skipped["монета"] += 1
            continue
        if streak_stop and stopped.get(r["sym"]) == day_of(t0)[:7]:
            skipped["серия"] += 1
            continue
        if r["sym"] in open_syms:
            # одна позиция на монету (так торгует бот) — новый вход, пока старая не закрылась, пропускаем
            skipped["занята"] += 1
            continue
        if kill_bps:
            # строка минуты входа X — известна к началу X (ход по закрытиям до X−1)
            i = bisect.bisect_right(minutes, t0 // 1_000_000) - 1
            if i >= 0 and vals[i] <= -kill_bps:
                skipped["btc"] += 1
                continue
        if day_stop_pct and realized.get(day_of(t0), 0.0) <= -day_stop_pct / 100 * deposit:
            skipped["день"] += 1
            continue
        if max_pos and len(open_pos) >= max_pos:
            skipped["позиций"] += 1
            continue
        if kills:
            # первое срабатывание после входа: строка K известна к началу K > t0; закрытие — свеча K
            j = bisect.bisect_right(kills, t0 // 1_000_000)
            if j < len(kills) and kills[j] * 1_000_000 < t1:
                px = klines.close(r["sym"], kills[j])
                if px is None:
                    no_kline += 1
                else:
                    # принудительное закрытие — рынок (тейкер), а не исходный выход круга (В-135 п.2,
                    # разбор Судьи `docs/research/reviews/fees-audit-2026-09-27.md`): если исходный выход
                    # был лимитным мейкером (тейк/горизонт), доплата за ногу = TAKER_LEG_BPS − MAKER_LEG_BPS
                    if reason in MAKER_EXIT_REASONS:
                        fee = r["fee"] + (TAKER_LEG_BPS - MAKER_LEG_BPS)
                    net = (px / r["entry"] - 1) * 1e4 * r["dir"] - fee
                    t1 = (kills[j] + MIN_MS) * 1_000_000
                    reason = "выключатель"
                    killed += 1
        pnl = net / 1e4 * r["usd"]
        if funding is not None:
            # издержка круга = Σ ставок отметок в (вход, выход] × номинал (В-135 п.3); t1 — уже фактический
            # (после принудительного закрытия, если оно было)
            pnl -= funding.cost_usd(r["sym"], t0 // 1_000_000, t1 // 1_000_000, r["dir"], r["usd"])
        open_pos.append((t1, pnl, r["usd"], r["sym"]))
        open_syms.add(r["sym"])
        taken.append({"t0": t0, "t1": t1, "sym": r["sym"], "pnl": pnl, "usd": r["usd"], "fill": r["fill"],
                      "reason": reason, "dir": r["dir"], "entry": r["entry"], "fee": fee, "net": net})
        peak_n = max(peak_n, len(open_pos))
        peak_usd = max(peak_usd, sum(u for _, _, u, _ in open_pos))
    settle(10**20)

    total = sum(x["pnl"] for x in taken)
    dd_closed_usd, dd_closed_pct = closed_drawdown(taken, deposit)
    mm = minute_curve(taken, klines, deposit, total)
    worst_day = min(realized.items(), key=lambda kv: kv[1]) if realized else ("—", 0.0)
    n = len(taken)
    tstats = taken_stats(taken)
    return {
        **tstats,
        "n": n, "skip": skipped, "killed": killed, "no_kline": no_kline,
        "fill": sum(x["fill"] for x in taken) / n if n else 0.0,
        "usd_mean": sum(x["usd"] for x in taken) / n if n else 0.0,
        "total_usd": total, "total_pct": total / deposit * 100,
        "dd_usd": mm["dd_usd"], "dd_pct": mm["dd_pct"],
        "dd_closed_usd": dd_closed_usd, "dd_closed_pct": dd_closed_pct,
        "rf": mm["rf"], "rec_days": mm["rec_days"], "unrecovered": mm["unrecovered"], "n_no_klines": mm["n_no_klines"],
        "worst_day": worst_day[0], "worst_day_usd": worst_day[1], "worst_day_pct": worst_day[1] / deposit * 100,
        "worst_trade_usd": min((x["pnl"] for x in taken), default=0.0),
        "win": sum(1 for x in taken if x["pnl"] > 0) / n if n else 0.0,
        "peak_n": peak_n, "peak_usd": peak_usd, "stress_pct": peak_usd * gap_pct / 100 / deposit * 100,
        "daily": {d: round(v, 2) for d, v in sorted(realized.items())},
        # кривая по закрытиям сделок (мс закрытия, $) — для KPI «до перехая» в часах (В-120); в --json не пишется
        "_closes": sorted((x["t1"] // 1_000_000, round(x["pnl"], 4)) for x in taken),
        # то же с монетой (TK-113, разрез по монетам); в --json не пишется
        "_closes_sym": sorted((x["t1"] // 1_000_000, round(x["pnl"], 4), x["sym"]) for x in taken),
    }
