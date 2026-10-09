#!/usr/bin/env python3
"""G11 (В-88): счёт депозита по сделкам бэктеста — реальные доллары, защиты, прирост / просадка / восстановление.

Деньги сделки — из самого бэктеста: заполненная позиция `qty × entry_vwap` (прогон с `--order-usd 500` — вся
лестница на $500, заполнение частичное по модели очереди) × `net_bps`. Сделки одной формы и набора идут общим
счётом во времени (вход — момент сигнала `t0_ns`, с запасом: заявка стоит до 30 мин; выход — `exit_ns`).

Защиты (каждый аргумент — список через запятую, печатается сетка):
- `--max-pos N` — не больше N позиций одновременно (0 — без потолка);
- `--day-stop-pct X` — реализованный убыток суток UTC ≥ X % депозита — до конца суток новых входов нет;
- `--btc-kill-bps K` — BTC за 1 ч ≤ −K bps: новых входов нет, открытые закрываются рыночным по закрытию
  минуты срабатывания (минутные свечи монеты `ref-<SYM>-1m.csv`; издержки круга — как у сделки в бэктесте).
  Строка минуты X файла режима — ход по закрытиям до X−1 (`regime.py`), известна к началу X, как у фильтра
  набора в Rust (`sets.rs:167`): вход смотрит строку своей минуты, срабатывание K > входа закрывает на свече K
  (минута задержки). До T-20 п.2 все три места брали строку на минуту старее;
- `--exclude-set имя=SYM,SYM` (повторяемый) — наборы исключённых монет; всегда есть «нет».

`--streak-stop N` (список через запятую, 0 — нет) — монета, закрывшая N убыточных сделок подряд за календарный месяц
(по закрытию, только свои взятые сделки — как увидит бот), до конца месяца новых входов не получает
(`skip["серия"]`); с нового месяца счёт серии заново. Вопрос владельца 25.09: монета с серией ещё и держит
депозит — эффект виден только с потолком позиций `--max-pos`.

`--drop SYM,SYM` — монеты вне торгового пула (В-105: TRXUSDT): их сделок нет ни в одном варианте, периоде и наборе
исключений — в отличие от `--exclude-set`, который сравнивается с «нет».

`--funding CSV` (В-135 п.3, по умолчанию выключено — старые прогоны воспроизводимы без изменений): издержка
фандинга по кругу = Σ ставок фактических отметок `funding_time_ms,funding_rate` в (вход, выход] × номинал позиции
(«+» — лонг платит, «−» — лонг получает); знак — как у комиссии, вычитается из pnl сделки после принудительного
закрытия (если оно было).

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
import datetime as dt
import importlib.util
import itertools
import json
import os
import sys

# Правила портфеля, минутная просадка (П1) и чтение прогонов — в `_lib/portfolio.py` (T-21, шаг
# после «`_lib` модулем», `docs/findings/t21-metrics-canon-2026-09-27.md` §3 п.5): формулы перенесены
# байт-в-байт, здесь — только CLI. Имена ниже — реэкспорт для внешних читателей, которые грузят этот
# файл по пути и берут атрибуты (`exit-sim.py`, `loss-days.py`, `fresh-days.py`, `p02-g86-analyze.py`,
# тесты `tools/compute/tests/test_portfolio_sim.py`) — адрес не меняется, поведение то же.
_lib_spec = importlib.util.spec_from_file_location(
    "_lib", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib", "__init__.py"))
_lib = importlib.util.module_from_spec(_lib_spec)
assert _lib_spec and _lib_spec.loader
_lib_spec.loader.exec_module(_lib)
_portfolio = _lib._portfolio  # модуль-объект (не только атрибуты) — нужен для синхронизации SIZE_MULT ниже

NS = _portfolio.NS
MIN_MS = _portfolio.MIN_MS
DAY_NS = _portfolio.DAY_NS
MAKER_LEG_BPS = _portfolio.MAKER_LEG_BPS
TAKER_LEG_BPS = _portfolio.TAKER_LEG_BPS
MAKER_EXIT_REASONS = _portfolio.MAKER_EXIT_REASONS
SIZE_MULT = _portfolio.SIZE_MULT

taken_stats = _portfolio.taken_stats
load_btc1h = _portfolio.load_btc1h
Klines = _portfolio.Klines
Funding = _portfolio.Funding
day_of = _portfolio.day_of
closed_drawdown = _portfolio.closed_drawdown
minute_curve = _portfolio.minute_curve
simulate = _portfolio.simulate


def load_run(home, run, set_name, form):
    """Обёртка вокруг `_lib.portfolio.load_run`: синхронизирует `SIZE_MULT` перед вызовом — это
    ДВА разных модуля (этот файл и `_lib/portfolio.py`), и присваивание `psim.SIZE_MULT = X` (как
    делает `main()` ниже и тест `test_size_mult_scales_usd_linearly`) само по себе меняет только
    атрибут ЭТОГО модуля, а `load_run` читает глобал своего модуля (`_lib.portfolio.SIZE_MULT`).
    Единственное место переноса не буквально построчно — остальная логика (сам `load_run`) не
    изменена, живёт в `_lib/portfolio.py`."""
    _portfolio.SIZE_MULT = SIZE_MULT
    return _portfolio.load_run(home, run, set_name, form)


def load_rounds(home, runs, set_name, form):
    """Та же синхронизация `SIZE_MULT`, что у `load_run` выше — `load_rounds` вызывает `load_run`
    СВОЕГО модуля (`_lib.portfolio`), минуя обёртку `load_run` этого файла."""
    _portfolio.SIZE_MULT = SIZE_MULT
    return _portfolio.load_rounds(home, runs, set_name, form)


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
    ap.add_argument("--streak-stop", default="0", help="N убыточных подряд за месяц — монета выключена до конца месяца; 0 — нет")
    ap.add_argument("--drop", default="", help="SYM,SYM — монеты вне торгового пула: их сделки не читаются вовсе")
    ap.add_argument("--stress-gap-pct", type=float, default=59.7)
    ap.add_argument("--size-mult", type=float, default=1.0,
                    help="множитель $ позиции каждой сделки (линейно, без пересчёта очереди); 1 — как было")
    ap.add_argument("--funding", default=None,
                    help="CSV symbol,funding_time_ms,funding_rate — издержка фандинга по факт. отметкам в (вход, "
                         "выход] (В-135 п.3); по умолчанию выключено, старые прогоны воспроизводимы")
    ap.add_argument("--json")
    ap.add_argument("--closes-out", help="JSON: вариант → период → потолок → [[мс закрытия, $], …] — только строки без "
                                         "дневного стопа, выключателя, исключений и серии (KPI «до перехая», В-120)")
    ap.add_argument("--closes-sym-out", help="как --closes-out, но [мс закрытия, $, монета] (TK-113, разрез по монетам)")
    a = ap.parse_args()
    global SIZE_MULT
    SIZE_MULT = a.size_mult
    _portfolio.SIZE_MULT = a.size_mult  # load_rounds → _portfolio.load_run напрямую, минуя обёртку выше
    funding = Funding(a.funding) if a.funding else None
    klines = Klines(a.klines)
    drop = set(x for x in a.drop.split(",") if x)
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
            # «набор1+набор2» — сигналы нескольких наборов одним счётом (смесь окон BTC, В-120): один и тот же сигнал
            # в двух наборах — вторая копия пропускается правилом «монета занята» (та же минута входа)
            rows = [r for s in set_name.split("+") for r in load_rounds(home, runs, s, form)]
            data[name] = ([r for r in rows if r["sym"] not in drop], btc)
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

    head = ["вариант", "период", "поз", "дн.стоп", "выкл", "искл", "серия", "сделок", "занята", "заполн", "прибыль$", "прирост%",
            "просадка%", "закр.дд%", "ф.восст", "восст.дн", "худш.сутки%", "пик$", "стресс%"]
    table, grid, closes, closes_sym = [], [], {}, {}
    for mp, ds, kb, (xname, xset), ss in itertools.product(floats(a.max_pos), floats(a.day_stop_pct), floats(a.btc_kill_bps),
                                                           excl, floats(a.streak_stop)):
        for vname, _, _, data in variants:
            for name in order:
                rows, btc = data[name]
                if not rows:
                    continue
                r = simulate(rows, btc, klines, a.deposit_usd, int(mp), ds, kb, xset, a.stress_gap_pct, int(ss),
                             funding)
                cl = r.pop("_closes")
                cls = r.pop("_closes_sym")
                if (a.closes_out or a.closes_sym_out) and not ds and not kb and xname == "нет" and not ss:
                    closes.setdefault(vname, {}).setdefault(name, {})[str(int(mp))] = cl
                    closes_sym.setdefault(vname, {}).setdefault(name, {})[str(int(mp))] = cls
                table.append([vname, name, int(mp) or "—", ds or "—", f"-{kb / 100:g}%" if kb else "—", xname, int(ss) or "—", r["n"],
                              r["skip"]["занята"], f"{r['fill'] * 100:.0f}%", f"{r['total_usd']:+.0f}", f"{r['total_pct']:+.2f}",
                              f"-{r['dd_pct']:.2f}", f"-{r['dd_closed_pct']:.2f}",
                              "—" if r["rf"] is None else f"{r['rf']:.1f}", f"{r['rec_days']:.1f}" + ("+" if r["unrecovered"] else ""),
                              f"{r['worst_day_pct']:+.2f}", f"{r['peak_usd']:.0f}", f"-{r['stress_pct']:.1f}"])
                grid.append({"variant": vname, "epoch": name, "max_pos": int(mp), "day_stop": ds, "kill": kb, "exclude": xname,
                             "streak_stop": int(ss),
                             **{k: (round(v, 4) if isinstance(v, float) else v) for k, v in r.items()}})
    widths = [max(len(str(x)) for x in col) for col in zip(head, *table)]
    for row in [head] + table:
        print("  ".join(str(x).rjust(w) for x, w in zip(row, widths)))
    if a.json:
        meta = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"), "deposit_usd": a.deposit_usd,
                "position_usd": a.position_usd,
                "stress_gap_pct": a.stress_gap_pct, "epochs": order, "drop": sorted(drop),
                "variants": [{"name": v[0], "set": v[1], "form": v[2]} for v in variants],
                "max_pos": [int(x) for x in floats(a.max_pos)], "day_stop": floats(a.day_stop_pct),
                "kill": floats(a.btc_kill_bps), "exclude": [{"name": n, "coins": sorted(s)} for n, s in excl], "grid": grid}
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, separators=(",", ":"))
        print(f"{len(grid)} строк → {a.json}", file=sys.stderr)
    if a.closes_out:
        with open(a.closes_out, "w", encoding="utf-8") as fh:
            json.dump(closes, fh, ensure_ascii=False, separators=(",", ":"))
    if a.closes_sym_out:
        with open(a.closes_sym_out, "w", encoding="utf-8") as fh:
            json.dump(closes_sym, fh, ensure_ascii=False, separators=(",", ":"))


if __name__ == "__main__":
    main()
