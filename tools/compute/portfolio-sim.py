#!/usr/bin/env python3
"""G11 (В-88): симуляция портфеля кандидата — лот от худшего случая, потолок экспозиции, выключатели.

Бэктест считает каждую сделку на одном лоте и не видит соседей; здесь сделки одной формы и набора
идут общим счётом во времени (вход — момент сигнала `t0_ns`, это с запасом: заявка стоит до 30 мин,
выход — `exit_ns`), и правила защиты применяются в момент входа:

- **лот от худшего случая:** лот = риск на сделку / худший прокид (`--risk-pct` / `--gap-pct`), то есть
  прокид на `gap` % через стоп стоит депозиту ровно `risk` %;
- **потолок экспозиции** `--cap-pct`: сумма открытых лотов, % депозита; вход сверх потолка пропускается;
- **выключатель дневного убытка** `--day-stop-pct`: реализованный убыток суток UTC ≥ X % — до конца суток
  новых входов нет;
- **выключатель по BTC** `--btc-kill-bps`: ход BTC за 1 ч в минуту входа ≤ −K bps — вход пропускается
  (закрыть открытые по выключателю отсюда нельзя: нужен путь цены; это следующий шаг);
- **исключение монет** `--exclude SYM,SYM`.

Числа защиты — владельца (В-88): каждый аргумент принимает список через запятую, печатается сетка.
Деньги — в % депозита, поэтому сам депозит не нужен. «Стресс» — убыток, если все позиции, открытые в
пик экспозиции, разом прокинет на `gap` % (сценарий обвала), тоже в % депозита.

    python3 portfolio-sim.py --epoch история=epochs/e-archive:b5/titrb-v1 --epoch запись=.:b5/titrb-v1 \\
        --set t-bid-btc1h-q1 --form ladder3x2..20w2-pct2-tr1x1-14400-ttl1800 \\
        --risk-pct 0.5,1 --gap-pct 15 --cap-pct 50,100,1000 --day-stop-pct 0,3 --btc-kill-bps 0,300
"""
import argparse
import bisect
import csv
import datetime as dt
import glob
import itertools
import os
import sys

NS = 1_000_000_000


def load_rounds(home, run, set_name, form):
    rows = []
    for f in sorted(glob.glob(os.path.join(home, run, "20*", set_name, "rounds.csv"))):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(line for line in fh if not line.startswith("#")):
                if r["form"] != form:
                    continue
                rows.append((int(r["t0_ns"]), int(r["exit_ns"]), r["symbol"], float(r["net_bps"]), r["reason"]))
    rows.sort()
    return rows


def load_btc1h(home):
    """минута (мс) -> ход BTC за 1 ч, bps; из study/regime/<сутки>.csv (ночь, regime.py)."""
    minutes, vals = [], []
    for f in sorted(glob.glob(os.path.join(home, "study", "regime", "20??-??-??.csv"))):
        with open(f, encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                v = r.get("btc_ret_1h_bps", "")
                if v:
                    minutes.append(int(r["minute_ms"]))
                    vals.append(float(v))
    order = sorted(range(len(minutes)), key=minutes.__getitem__)
    return [minutes[i] for i in order], [vals[i] for i in order]


def btc_at(btc, t_ns):
    minutes, vals = btc
    i = bisect.bisect_right(minutes, t_ns // 1_000_000) - 1
    return vals[i] if i >= 0 else None


def day_of(t_ns):
    return dt.datetime.fromtimestamp(t_ns / NS, dt.timezone.utc).strftime("%Y-%m-%d")


def simulate(rows, btc, risk, gap, cap, day_stop, kill_bps, exclude):
    lot = risk / gap * 100.0  # % депозита на сделку
    open_pos = []  # (exit_ns, pnl_pct)
    realized_by_day = {}
    skipped = {"потолок": 0, "день": 0, "btc": 0, "монета": 0}
    taken = []
    peak_exp = peak_n = 0.0
    for t0, t1, sym, net, reason in rows:
        # закрыть всё, что вышло до этого входа — реализованный убыток суток считается по выходам
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
            b = btc_at(btc, t0)
            if b is not None and b <= -kill_bps:
                skipped["btc"] += 1
                continue
        if day_stop and realized_by_day.get(day_of(t0), 0.0) <= -day_stop:
            skipped["день"] += 1
            continue
        if cap and (len(open_pos) + 1) * lot > cap + 1e-9:
            skipped["потолок"] += 1
            continue
        pnl = lot * net / 1e4
        open_pos.append((t1, pnl))
        taken.append((t0, t1, sym, pnl, reason, net))
        exp = len(open_pos) * lot
        if exp > peak_exp:
            peak_exp, peak_n = exp, len(open_pos)
    for e, p in open_pos:
        d = day_of(e)
        realized_by_day[d] = realized_by_day.get(d, 0.0) + p
    # кривая по выходам: итог, худшие сутки, просадка
    eq = peak = dd = 0.0
    for _, t1, _, p, _, _ in sorted(taken, key=lambda x: x[1]):
        eq += p
        peak = max(peak, eq)
        dd = min(dd, eq - peak)
    worst_day = min(realized_by_day.items(), key=lambda kv: kv[1]) if realized_by_day else ("—", 0.0)
    worst_trade = min((x[3] for x in taken), default=0.0)
    worst_net = min((x[5] for x in taken), default=0.0)
    return {
        "lot": lot, "n": len(taken), "skip": skipped, "total": eq, "dd": dd,
        "worst_day": worst_day, "worst_trade": worst_trade, "worst_net_bps": worst_net,
        "peak_exp": peak_exp, "peak_n": peak_n, "stress": peak_exp * gap / 100.0,
    }


def floats(s):
    return [float(x) for x in s.split(",") if x != ""]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--epoch", action="append", required=True, help="имя=<дом>:<каталог прогона от дома>")
    ap.add_argument("--set", required=True)
    ap.add_argument("--form", required=True)
    ap.add_argument("--risk-pct", default="1", help="риск на сделку при худшем прокиде, % депозита")
    ap.add_argument("--gap-pct", default="15", help="худший прокид через стоп, %")
    ap.add_argument("--cap-pct", default="0", help="потолок открытых лотов, % депозита; 0 — нет")
    ap.add_argument("--day-stop-pct", default="0", help="дневной убыток для выключателя, % депозита; 0 — нет")
    ap.add_argument("--btc-kill-bps", default="0", help="BTC за 1 ч ≤ −K bps — вход пропускается; 0 — нет")
    ap.add_argument("--exclude", default="", help="монеты через запятую")
    ap.add_argument("--csv", help="сетка целиком в CSV")
    a = ap.parse_args()
    exclude = set(x for x in a.exclude.split(",") if x)

    epochs = []
    for spec in a.epoch:
        name, rest = spec.split("=", 1)
        home, run = rest.split(":", 1)
        rows = load_rounds(home, run, a.set, a.form)
        if not rows:
            sys.exit(f"{name}: нет сделок формы {a.form} набора {a.set} в {home}/{run}")
        epochs.append((name, rows, load_btc1h(home)))

    head = ["эпоха", "риск%", "прокид%", "потолок%", "день%", "btc_bps", "лот%", "сделок", "пропуск(потолок/день/btc/монета)",
            "итог%", "просадка%", "худшие_сутки", "худшие_сутки%", "худшая_сделка%", "худшая_сделка_bps",
            "пик_экспоз%", "пик_позиций", "стресс%"]
    out = []
    grid = itertools.product(floats(a.risk_pct), floats(a.gap_pct), floats(a.cap_pct), floats(a.day_stop_pct),
                             floats(a.btc_kill_bps))
    for risk, gap, cap, day_stop, kill in grid:
        for name, rows, btc in epochs:
            r = simulate(rows, btc, risk, gap, cap, day_stop, kill, exclude)
            s = r["skip"]
            out.append([name, risk, gap, cap or "—", day_stop or "—", int(kill) or "—", f"{r['lot']:.2f}", r["n"],
                        f"{s['потолок']}/{s['день']}/{s['btc']}/{s['монета']}", f"{r['total']:+.2f}", f"{r['dd']:.2f}",
                        r["worst_day"][0], f"{r['worst_day'][1]:+.2f}", f"{r['worst_trade']:+.2f}",
                        f"{r['worst_net_bps']:.0f}", f"{r['peak_exp']:.0f}", r["peak_n"], f"{r['stress']:.1f}"])
    widths = [max(len(str(x)) for x in col) for col in zip(head, *out)]
    for row in [head] + out:
        print("  ".join(str(x).rjust(w) for x, w in zip(row, widths)))
    if a.csv:
        with open(a.csv, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(head)
            w.writerows(out)


if __name__ == "__main__":
    main()
