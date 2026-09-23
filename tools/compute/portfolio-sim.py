#!/usr/bin/env python3
"""G11 (В-88): симуляция портфеля кандидата — лот, потолок экспозиции, выключатели.

Бэктест считает каждую сделку на одном лоте и не видит соседей; здесь сделки одной формы и набора
идут общим счётом во времени (вход — момент сигнала `t0_ns`, это с запасом: заявка стоит до 30 мин,
выход — `exit_ns`), и правила защиты применяются так:

- **лот** `--lot-pct`: % депозита на сделку; «риск на сделку» = лот × худший прокид сценария (ниже);
- **потолок экспозиции** `--cap-pct`: сумма открытых лотов, % депозита; вход сверх потолка пропускается;
- **выключатель дневного убытка** `--day-stop-pct`: реализованный убыток суток UTC ≥ X % — до конца суток
  новых входов нет;
- **выключатель по BTC** `--btc-kill-bps`: ход BTC за 1 ч ≤ −K bps — новых входов нет, а открытые позиции
  закрываются рыночным по закрытию следующей минуты (минутные свечи монеты `ref-<SYM>-1m.csv`, издержки
  круга — те же, что у сделки в бэктесте: net − gross); нет свечи — сделка остаётся как была (счётчик);
- **исключение монет** `--exclude SYM,SYM`.

Каждый аргумент — список через запятую; печатается сетка (или `--json` для дашборда). Деньги — в % депозита.
**Стресс** — сценарий «в провал вошли на пике позиций»: пик открытых лотов × худший прокид при данном
выключателе (`--stress-gap K:%,…` — из `crash-gaps.py` на обвале 10.10.2025; `0` — без выключателя).

    python3 portfolio-sim.py --epoch история=epochs/e-archive:b5/titrc-v1 --epoch запись=.:b5/titrc-v1 \\
        --klines study/klines --variant кандидат=t-bid-btc1h-q1/ladder3x2..20w2-pct2-tr1x1-14400-ttl1800 \\
        --lot-pct 2,5,10 --cap-pct 0,50 --day-stop-pct 0,2 --btc-kill-bps 0,150 \\
        --stress-gap 0:59.7,150:3.2 [--json protection.json]
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


def load_rounds(home, runs, set_name, form):
    """Сделки формы из первого прогона (списка через запятую), где она есть."""
    for run in runs.split(","):
        rows = load_run(home, run, set_name, form)
        if rows:
            return rows
    return []


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
                rows.append((int(r["t0_ns"]), int(r["exit_ns"]), r["symbol"], net, r["reason"], entry, gross - net))
    rows.sort()
    return rows


def load_btc1h(home):
    """Минуты (мс) и ход BTC за 1 ч, bps — из study/regime/<сутки>.csv (ночь, regime.py)."""
    pairs = []
    for f in sorted(glob.glob(os.path.join(home, "study", "regime", "20??-??-??.csv"))):
        with open(f, encoding="utf-8") as fh:
            pairs += [(int(r["minute_ms"]), float(r["btc_ret_1h_bps"])) for r in csv.DictReader(fh) if r.get("btc_ret_1h_bps")]
    pairs.sort()
    return [m for m, _ in pairs], [v for _, v in pairs]


class Klines:
    """Закрытия минутных свечей монет из каталогов `ref-<SYM>-1m.csv` (минуты всех каталогов вместе)."""

    def __init__(self, dirs):
        self.dirs, self.cache = dirs, {}

    def close(self, sym, minute_ms):
        if sym not in self.cache:
            self.cache[sym] = {}
            for d in self.dirs:
                p = os.path.join(d, f"ref-{sym}-1m.csv")
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as fh:
                        self.cache[sym].update((int(r["minute_ms"]), float(r["close"])) for r in csv.DictReader(fh))
        return self.cache[sym].get(minute_ms)


def day_of(t_ns):
    return dt.datetime.fromtimestamp(t_ns / NS, dt.timezone.utc).strftime("%Y-%m-%d")


def kill_minutes(btc, kill_bps):
    minutes, vals = btc
    return [m for m, v in zip(minutes, vals) if v <= -kill_bps]


def simulate(rows, btc, klines, lot, cap, day_stop, kill_bps, exclude):
    minutes, vals = btc
    kills = kill_minutes(btc, kill_bps) if kill_bps else []
    open_pos = []  # (exit_ns, pnl_pct)
    realized_by_day = {}
    skipped = {"потолок": 0, "день": 0, "btc": 0, "монета": 0}
    n_killed = n_no_kline = 0
    taken = []
    peak_exp = peak_n = 0.0
    for t0, t1, sym, net, reason, entry, fee in rows:
        still = []
        for e, p in open_pos:
            if e <= t0:
                d = day_of(e)
                realized_by_day[d] = realized_by_day.get(d, 0.0) + p
            else:
                still.append((e, p))
        open_pos = still
        if sym in exclude:
            skipped["монета"] += 1
            continue
        if kill_bps:
            # последняя закрытая минута до входа (значение минуты известно на её закрытии)
            i = bisect.bisect_right(minutes, t0 // 1_000_000 - MIN_MS) - 1
            if i >= 0 and vals[i] <= -kill_bps:
                skipped["btc"] += 1
                continue
        if day_stop and realized_by_day.get(day_of(t0), 0.0) <= -day_stop:
            skipped["день"] += 1
            continue
        if cap and (len(open_pos) + 1) * lot > cap + 1e-9:
            skipped["потолок"] += 1
            continue
        if kills:
            # первая минута выключателя строго после входа и до выхода — закрыть по закрытию следующей минуты
            j = bisect.bisect_right(kills, t0 // 1_000_000 - MIN_MS)
            if j < len(kills) and kills[j] * 1_000_000 < t1:
                px = klines.close(sym, kills[j] + MIN_MS)
                if px is None:
                    n_no_kline += 1
                else:
                    net = (px / entry - 1) * 1e4 - fee
                    t1 = (kills[j] + 2 * MIN_MS) * 1_000_000
                    reason = "выключатель"
                    n_killed += 1
        pnl = lot * net / 1e4
        open_pos.append((t1, pnl))
        taken.append((t0, t1, sym, pnl, reason, net))
        exp = len(open_pos) * lot
        if exp > peak_exp:
            peak_exp, peak_n = exp, len(open_pos)
    for e, p in open_pos:
        d = day_of(e)
        realized_by_day[d] = realized_by_day.get(d, 0.0) + p
    eq = peak = dd = 0.0
    for _, t1, _, p, _, _ in sorted(taken, key=lambda x: x[1]):
        eq += p
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    worst_day = min(realized_by_day.items(), key=lambda kv: kv[1]) if realized_by_day else ("—", 0.0)
    wins = sum(1 for x in taken if x[3] > 0)
    return {
        "n": len(taken), "skip": skipped, "killed": n_killed, "no_kline": n_no_kline, "total": eq, "dd": dd,
        "worst_day": worst_day, "worst_trade": min((x[3] for x in taken), default=0.0),
        "worst_net_bps": min((x[5] for x in taken), default=0.0), "win": wins / len(taken) if taken else 0.0,
        "peak_exp": peak_exp, "peak_n": peak_n, "daily": dict(sorted(realized_by_day.items())),
    }


def floats(s):
    return [float(x) for x in s.split(",") if x != ""]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--epoch", action="append", required=True, help="имя=<дом>:<прогон>[,<прогон>…] — форма берётся из первого прогона, где она есть")
    ap.add_argument("--variant", action="append", required=True, help="имя=<набор>/<форма>")
    ap.add_argument("--klines", action="append", default=[], help="каталоги минутных свечей монет (ref-klines.py)")
    ap.add_argument("--lot-pct", default="10", help="лот, % депозита")
    ap.add_argument("--cap-pct", default="0", help="потолок открытых лотов, % депозита; 0 — нет")
    ap.add_argument("--day-stop-pct", default="0", help="дневной убыток для выключателя, % депозита; 0 — нет")
    ap.add_argument("--btc-kill-bps", default="0", help="BTC за 1 ч ≤ −K bps — закрыть всё и не входить; 0 — нет")
    ap.add_argument("--stress-gap", default="0:59.7", help="K:худший прокид % при выключателе K (0 — без него)")
    ap.add_argument("--exclude", default="", help="монеты через запятую")
    ap.add_argument("--json", help="сетка целиком и дневной результат — для дашборда")
    a = ap.parse_args()
    exclude = set(x for x in a.exclude.split(",") if x)
    stress_gap = {int(float(k)): float(v) for k, v in (x.split(":") for x in a.stress_gap.split(","))}
    klines = Klines(a.klines)

    epochs = []
    for spec in a.epoch:
        name, rest = spec.split("=", 1)
        home, run = rest.split(":", 1)
        epochs.append((name, home, run, load_btc1h(home)))
    variants = []
    for spec in a.variant:
        vname, rest = spec.split("=", 1)
        set_name, form = rest.split("/", 1)
        per_epoch = {}
        for name, home, run, _ in epochs:
            per_epoch[name] = load_rounds(home, run, set_name, form)
            if not per_epoch[name]:
                print(f"!! {vname}/{name}: нет сделок {set_name}/{form} в {home}/{run}", file=sys.stderr)
        variants.append((vname, set_name, form, per_epoch))

    head = ["вариант", "эпоха", "лот%", "потолок%", "день%", "btc%", "сделок", "пропуск п/д/б/м", "выкл", "итог%",
            "просадка%", "худшие_сутки%", "худшая_сделка%", "пик_позиций", "стресс%"]
    table, grid = [], []
    combos = itertools.product(floats(a.lot_pct), floats(a.cap_pct), floats(a.day_stop_pct), floats(a.btc_kill_bps))
    for lot, cap, day_stop, kill in combos:
        for vname, _, _, per_epoch in variants:
            for name, _, _, btc in epochs:
                rows = per_epoch[name]
                if not rows:
                    continue
                r = simulate(rows, btc, klines, lot, cap, day_stop, kill, exclude)
                gap = stress_gap.get(int(kill), stress_gap.get(0, 0.0))
                stress = r["peak_n"] * lot * gap / 100
                s = r["skip"]
                table.append([vname, name, lot, cap or "—", day_stop or "—", f"-{kill / 100:g}" if kill else "—", r["n"],
                              f"{s['потолок']}/{s['день']}/{s['btc']}/{s['монета']}", r["killed"], f"{r['total']:+.2f}",
                              f"{r['dd']:.2f}", f"{r['worst_day'][1]:+.2f}", f"{r['worst_trade']:+.2f}", r["peak_n"],
                              f"{stress:.1f}"])
                grid.append({"variant": vname, "epoch": name, "lot": lot, "cap": cap, "day_stop": day_stop, "kill": kill,
                             "n": r["n"], "skip": s, "killed": r["killed"], "no_kline": r["no_kline"],
                             "total": round(r["total"], 3), "dd": round(r["dd"], 3), "worst_day": r["worst_day"][0],
                             "worst_day_pct": round(r["worst_day"][1], 3), "worst_trade": round(r["worst_trade"], 3),
                             "win": round(r["win"], 3), "peak_n": r["peak_n"], "stress": round(stress, 2),
                             "stress_gap": gap, "daily": {d: round(v, 4) for d, v in r["daily"].items()}})
    widths = [max(len(str(x)) for x in col) for col in zip(head, *table)]
    for row in [head] + table:
        print("  ".join(str(x).rjust(w) for x, w in zip(row, widths)))
    if a.json:
        meta = {"generated_utc": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M"),
                "epochs": [e[0] for e in epochs], "runs": {e[0]: f"{e[1]}:{e[2]}" for e in epochs},
                "variants": [{"name": v[0], "set": v[1], "form": v[2]} for v in variants],
                "lot": floats(a.lot_pct), "cap": floats(a.cap_pct), "day_stop": floats(a.day_stop_pct),
                "kill": floats(a.btc_kill_bps), "stress_gap": stress_gap, "grid": grid}
        with open(a.json, "w", encoding="utf-8") as fh:
            json.dump(meta, fh, ensure_ascii=False, separators=(",", ":"))
        print(f"{len(grid)} строк → {a.json}", file=sys.stderr)


if __name__ == "__main__":
    main()
