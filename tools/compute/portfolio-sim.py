#!/usr/bin/env python3
"""G11 (В-88): счёт депозита по сделкам бэктеста — реальные доллары, защиты, прирост / просадка / восстановление.

Деньги сделки — из самого бэктеста: заполненная позиция `qty × entry_vwap` (прогон с `--order-usd 500` — вся
лестница на $500, заполнение частичное по модели очереди) × `net_bps`. Сделки одной формы и набора идут общим
счётом во времени (вход — момент сигнала `t0_ns`, с запасом: заявка стоит до 30 мин; выход — `exit_ns`).

Защиты (каждый аргумент — список через запятую, печатается сетка):
- `--max-pos N` — не больше N позиций одновременно (0 — без потолка);
- `--day-stop-pct X` — реализованный убыток суток UTC ≥ X % депозита — до конца суток новых входов нет;
- `--btc-kill-bps K` — BTC за 1 ч ≤ −K bps: новых входов нет, открытые закрываются рыночным по закрытию
  следующей минуты (минутные свечи монеты `ref-<SYM>-1m.csv`; издержки круга — как у сделки в бэктесте);
- `--exclude-set имя=SYM,SYM` (повторяемый) — наборы исключённых монет; всегда есть «нет».

Отчёт (депозит `--deposit-usd`): прирост % = прибыль / депозит; макс. просадка % — от пика капитала;
фактор восстановления = прибыль / макс. просадка ($); восстановление — самый долгий отрезок от пика капитала до
нового пика, дней (не вышел к концу периода — помечается). **Стресс** — сценарий «провал застал пик позиций»:
пик заполненных позиций × худший прокид (`--stress-gap-pct`, обвал 10.10.2025: −59.7 % без выключателя, замер
`crash-gaps.py`), % депозита.

Капитал между сделками переоценивается по минутам (mark-to-market, `--klines`): открытая позиция несёт
нереализованный P&L по закрытию минутной свечи монеты, с направлением сделки и издержками круга — как у
самой сделки; нет свечи на минуту — последняя известная, монета без свечей вовсе — по закрытиям
(`n_no_klines`). `dd_pct/dd_usd/rf/rec_days/unrecovered` — по этой минутной кривой; прежняя модель «растёт
только на закрытии» — в `dd_closed_pct/dd_closed_usd`. Одна позиция на монету: пока старая не закрылась,
новый вход по ней пропускается (`skip["занята"]`) — так торгует бот.

    python3 portfolio-sim.py --epoch история=epochs/e-archive:b5/titrc-u500-trail --epoch запись=.:b5/titrc-u500-trail \\
        --join сентябрь=история+запись --klines study/klines \\
        --variant кандидат=t-bid-btc1h-q1/ladder3x2..20w2-pct2-tr1x1-14400-ttl1800 \\
        --deposit-usd 2500 --position-usd 500 --max-pos 1,2,3,5 --day-stop-pct 0,2 --btc-kill-bps 0,150 \\
        --exclude-set прокиды=STORJUSDT,… [--json protection.json]
"""
import argparse
import bisect
import csv
import datetime as dt
import glob
import itertools
import json
import os
import sys

NS = 1_000_000_000
MIN_MS = 60_000
DAY_NS = 86_400 * NS


def load_run(home, run, set_name, form):
    rows = []
    for f in sorted(glob.glob(os.path.join(home, run, "20*", set_name, "rounds.csv"))):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(line for line in fh if not line.startswith("#")):
                if r["form"] != form:
                    continue
                entry = float(r.get("entry_vwap") or 0) or float(r["entry_px"])
                gross = (float(r["exit_px"]) / entry - 1) * 1e4 * int(r["dir"])
                net = float(r["net_bps"])
                rows.append({"t0": int(r["t0_ns"]), "t1": int(r["exit_ns"]), "sym": r["symbol"], "net": net,
                             "reason": r["reason"], "entry": entry, "fee": gross - net, "dir": int(r["dir"]),
                             "usd": float(r["qty"]) * entry, "fill": float(r.get("fill_frac") or 1.0)})
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


def day_of(t_ns):
    return dt.datetime.fromtimestamp(t_ns / NS, dt.timezone.utc).strftime("%Y-%m-%d")


def closed_drawdown(taken, deposit):
    """Просадка по капиталу прежней модели: растёт только на закрытии сделки (шаг по `t1`), без
    переоценки открытых позиций между сделками."""
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
    """Просадка и восстановление по минутной переоценке (mark-to-market) открытых позиций: капитал
    между сделками движется по закрытию минутной свечи монеты, направление и издержки круга — как у
    самой сделки. Нет свечи на минуту — берём последнюю известную (`Klines.last_close`); монета без
    свечей вовсе остаётся на модели «по закрытиям» (её берёт n_no_klines).

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


def simulate(rows, btc, klines, deposit, max_pos, day_stop_pct, kill_bps, exclude, gap_pct):
    minutes, vals = btc
    kills = [m for m, v in zip(minutes, vals) if v <= -kill_bps] if kill_bps else []
    open_pos = []  # (t1, pnl_usd, usd, sym)
    open_syms = set()
    realized = {}
    skipped = {"позиций": 0, "день": 0, "btc": 0, "монета": 0, "занята": 0}
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
            else:
                keep.append((t1, p, u, sym))
        open_pos = keep

    for r in sorted(rows, key=lambda x: x["t0"]):
        t0, t1, net, reason = r["t0"], r["t1"], r["net"], r["reason"]
        settle(t0)
        if r["sym"] in exclude:
            skipped["монета"] += 1
            continue
        if r["sym"] in open_syms:
            # одна позиция на монету (так торгует бот) — новый вход, пока старая не закрылась, пропускаем
            skipped["занята"] += 1
            continue
        if kill_bps:
            # последняя закрытая минута до входа (значение минуты известно на её закрытии)
            i = bisect.bisect_right(minutes, t0 // 1_000_000 - MIN_MS) - 1
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
            j = bisect.bisect_right(kills, t0 // 1_000_000 - MIN_MS)
            if j < len(kills) and kills[j] * 1_000_000 < t1:
                px = klines.close(r["sym"], kills[j] + MIN_MS)
                if px is None:
                    no_kline += 1
                else:
                    net = (px / r["entry"] - 1) * 1e4 * r["dir"] - r["fee"]
                    t1 = (kills[j] + 2 * MIN_MS) * 1_000_000
                    reason = "выключатель"
                    killed += 1
        pnl = net / 1e4 * r["usd"]
        open_pos.append((t1, pnl, r["usd"], r["sym"]))
        open_syms.add(r["sym"])
        taken.append({"t0": t0, "t1": t1, "sym": r["sym"], "pnl": pnl, "usd": r["usd"], "fill": r["fill"],
                      "reason": reason, "dir": r["dir"], "entry": r["entry"], "fee": r["fee"]})
        peak_n = max(peak_n, len(open_pos))
        peak_usd = max(peak_usd, sum(u for _, _, u, _ in open_pos))
    settle(10**20)

    total = sum(x["pnl"] for x in taken)
    dd_closed_usd, dd_closed_pct = closed_drawdown(taken, deposit)
    mm = minute_curve(taken, klines, deposit, total)
    worst_day = min(realized.items(), key=lambda kv: kv[1]) if realized else ("—", 0.0)
    n = len(taken)
    return {
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
    }


def floats(s):
    return [float(x) for x in s.split(",") if x != ""]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--epoch", action="append", required=True, help="имя=<дом>:<прогон>[,<прогон>…]")
    ap.add_argument("--join", action="append", default=[], help="имя=эпоха+эпоха — один счёт подряд")
    ap.add_argument("--variant", action="append", required=True, help="имя=<набор>/<форма>")
    ap.add_argument("--klines", action="append", default=[], help="каталоги минутных свечей монет")
    ap.add_argument("--deposit-usd", type=float, required=True)
    ap.add_argument("--position-usd", type=float, required=True, help="размер позиции прогона (--order-usd) — для подписи")
    ap.add_argument("--max-pos", default="0", help="не больше N позиций одновременно; 0 — без потолка")
    ap.add_argument("--day-stop-pct", default="0", help="дневной убыток, % депозита; 0 — нет")
    ap.add_argument("--btc-kill-bps", default="0", help="BTC за 1 ч ≤ −K bps — закрыть всё и не входить; 0 — нет")
    ap.add_argument("--exclude-set", action="append", default=[], help="имя=SYM,SYM — набор исключённых монет")
    ap.add_argument("--stress-gap-pct", type=float, default=59.7)
    ap.add_argument("--json")
    a = ap.parse_args()
    klines = Klines(a.klines)
    excl = [("нет", set())] + [(s.split("=", 1)[0], set(x for x in s.split("=", 1)[1].split(",") if x)) for s in a.exclude_set]

    epochs = {}
    order = []
    for spec in a.epoch:
        name, rest = spec.split("=", 1)
        home, runs = rest.split(":", 1)
        epochs[name] = (home, runs, load_btc1h(home))
        order.append(name)
    joins = {}
    for spec in a.join:
        name, parts = spec.split("=", 1)
        joins[name] = parts.split("+")
        order.append(name)

    variants = []
    for spec in a.variant:
        vname, rest = spec.split("=", 1)
        set_name, form = rest.split("/", 1)
        data = {}
        for name, (home, runs, btc) in epochs.items():
            data[name] = (load_rounds(home, runs, set_name, form), btc)
            if not data[name][0]:
                print(f"!! {vname}/{name}: нет сделок {set_name}/{form} в {home}/{runs}", file=sys.stderr)
        for name, parts in joins.items():
            rows, pairs = [], set()
            for p in parts:
                rows += data[p][0]
                pairs |= set(zip(*data[p][1]))
            pairs = sorted(pairs)
            data[name] = (rows, ([m for m, _ in pairs], [v for _, v in pairs]))
        variants.append((vname, set_name, form, data))

    head = ["вариант", "период", "поз", "дн.стоп", "выкл", "искл", "сделок", "занята", "заполн", "прибыль$", "прирост%",
            "просадка%", "закр.дд%", "ф.восст", "восст.дн", "худш.сутки%", "пик$", "стресс%"]
    table, grid = [], []
    for mp, ds, kb, (xname, xset) in itertools.product(floats(a.max_pos), floats(a.day_stop_pct), floats(a.btc_kill_bps), excl):
        for vname, _, _, data in variants:
            for name in order:
                rows, btc = data[name]
                if not rows:
                    continue
                r = simulate(rows, btc, klines, a.deposit_usd, int(mp), ds, kb, xset, a.stress_gap_pct)
                table.append([vname, name, int(mp) or "—", ds or "—", f"-{kb / 100:g}%" if kb else "—", xname, r["n"],
                              r["skip"]["занята"], f"{r['fill'] * 100:.0f}%", f"{r['total_usd']:+.0f}", f"{r['total_pct']:+.2f}",
                              f"-{r['dd_pct']:.2f}", f"-{r['dd_closed_pct']:.2f}",
                              "—" if r["rf"] is None else f"{r['rf']:.1f}", f"{r['rec_days']:.1f}" + ("+" if r["unrecovered"] else ""),
                              f"{r['worst_day_pct']:+.2f}", f"{r['peak_usd']:.0f}", f"-{r['stress_pct']:.1f}"])
                grid.append({"variant": vname, "epoch": name, "max_pos": int(mp), "day_stop": ds, "kill": kb, "exclude": xname,
                             **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}})
    widths = [max(len(str(x)) for x in col) for col in zip(head, *table)]
    for row in [head] + table:
        print("  ".join(str(x).rjust(w) for x, w in zip(row, widths)))
    if a.json:
        meta = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), "deposit_usd": a.deposit_usd,
                "position_usd": a.position_usd,
                "stress_gap_pct": a.stress_gap_pct, "epochs": order,
                "variants": [{"name": v[0], "set": v[1], "form": v[2]} for v in variants],
                "max_pos": [int(x) for x in floats(a.max_pos)], "day_stop": floats(a.day_stop_pct),
                "kill": floats(a.btc_kill_bps), "exclude": [{"name": n, "coins": sorted(s)} for n, s in excl], "grid": grid}
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, separators=(",", ":"))
        print(f"{len(grid)} строк → {a.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
