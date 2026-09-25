#!/usr/bin/env python3
"""Разбор убыточных дней счёта (владелец 25.09: «сначала все убыточные дни разобрать, потом собирать гипотезы»).

Сделки — как в счёте дашборда (`portfolio-sim.py` без потолка и выключателя): одна позиция на монету, монеты
`--drop` вне пула, день — по закрытию сделки (UTC, как столбики «Результат по дням»). Для каждого убыточного дня:
итог по причинам выхода, эпизоды входа (перерыв между входами > `--gap-min` — новый эпизод: одна просадка рынка),
ход BTC от первого входа эпизода (минимум за время его сделок и к последнему выходу), BTC 1 ч / 4 ч на входе
(файлы режима), худшие сделки. Время в отчёте — GMT+4 (часы владельца), сутки — UTC. Только факты; гипотезы — потом.

    python3 loss-days.py --epoch август=epochs/e-aug:b5/titrc-u500r \\
        --epoch сентябрь=epochs/e-archive:b5/titrc-u500r --epoch сентябрь=.:b5/titrc-u500r \\
        --set t-bid-btc4h-q1 --form ladder3x2..20w2-pct2-tr1x1-14400-ttl1800 --drop TRXUSDT \\
        --csv study/loss-days-btc4h.csv > study/loss-days-btc4h.txt
"""
import argparse
import bisect
import csv
import datetime as dt
import importlib.util
import os
from collections import defaultdict

_spec = importlib.util.spec_from_file_location("psim", os.path.join(os.path.dirname(os.path.abspath(__file__)), "portfolio-sim.py"))
psim = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(psim)

NS, MIN_MS = 1_000_000_000, 60_000
LOCAL = dt.timezone(dt.timedelta(hours=4))


def utc_day(t_ns):
    return dt.datetime.fromtimestamp(t_ns / NS, dt.timezone.utc).strftime("%Y-%m-%d")


def local_hm(t_ns):
    return dt.datetime.fromtimestamp(t_ns / NS, LOCAL).strftime("%d.%m %H:%M")


class Btc:
    """Закрытия минутных свечей BTC (`study/regime/ref-BTCUSDT-1m.csv` эпох) и ходы 1 ч / 4 ч (файлы режима)."""

    def __init__(self, homes):
        self.close, self.regime = {}, {}
        self.regime_dirs = [os.path.join(h, "study", "regime") for h in homes]
        for d in self.regime_dirs:
            p = os.path.join(d, "ref-BTCUSDT-1m.csv")
            if os.path.exists(p):
                with open(p, encoding="utf-8") as f:
                    self.close.update((int(r["minute_ms"]), float(r["close"])) for r in csv.DictReader(f))
        self.keys = sorted(self.close)

    def at(self, t_ns):
        i = bisect.bisect_right(self.keys, t_ns // 1_000_000) - 1
        return self.close[self.keys[i]] if i >= 0 else None

    def low(self, t0_ns, t1_ns):
        lo, hi = bisect.bisect_left(self.keys, t0_ns // 1_000_000), bisect.bisect_right(self.keys, t1_ns // 1_000_000)
        vals = [self.close[k] for k in self.keys[lo:hi]]
        return min(vals) if vals else None

    def ret(self, t_ns, col):
        day = utc_day(t_ns)
        if day not in self.regime:
            rows = {}
            for d in self.regime_dirs:
                p = os.path.join(d, f"{day}.csv")
                if os.path.exists(p):
                    with open(p, encoding="utf-8") as f:
                        for r in csv.DictReader(f):
                            # файлы соседних эпох перекрываются (~2 суток на файл): пустая строка не затирает полную
                            m = int(r["minute_ms"])
                            if m not in rows or rows[m].get("btc_ret_4h_bps") in (None, ""):
                                rows[m] = r
            self.regime[day] = rows
        m = (t_ns // 1_000_000) // MIN_MS * MIN_MS - MIN_MS  # последняя закрытая минута до входа
        v = self.regime[day].get(m, {}).get(col)
        return float(v) if v not in (None, "") else None


def bps(a, b):
    return (b / a - 1) * 1e4 if a and b else None


def fmt(v, suf=""):
    return "—" if v is None else f"{v:+.0f}{suf}"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--epoch", action="append", required=True, help="месяц=<дом>:<прогон>; одно имя — несколько эпох подряд")
    ap.add_argument("--set", required=True)
    ap.add_argument("--form", required=True)
    ap.add_argument("--drop", default="")
    ap.add_argument("--gap-min", type=int, default=60)
    ap.add_argument("--csv")
    a = ap.parse_args()
    drop = set(x for x in a.drop.split(",") if x)
    months = defaultdict(list)
    for spec in a.epoch:
        name, rest = spec.split("=", 1)
        home, run = rest.split(":", 1)
        months[name].append((home, run))

    csv_rows = []
    for month, parts in months.items():
        btc = Btc([h for h, _ in parts])
        rows = []
        for home, run in parts:
            rows += [r for r in psim.load_rounds(home, run, a.set, a.form) if r["sym"] not in drop]
        # одна позиция на монету — как счёт дашборда
        taken, busy_until = [], {}
        for r in sorted(rows, key=lambda x: x["t0"]):
            if busy_until.get(r["sym"], 0) > r["t0"]:
                continue
            busy_until[r["sym"]] = r["t1"]
            r["pnl"] = r["net"] / 1e4 * r["usd"]
            r["btc_move"] = bps(btc.at(r["t0"]), btc.at(r["t1"]))
            r["btc_low"] = bps(btc.at(r["t0"]), btc.low(r["t0"], r["t1"]))
            r["btc1h"], r["btc4h"] = btc.ret(r["t0"], "btc_ret_1h_bps"), btc.ret(r["t0"], "btc_ret_4h_bps")
            taken.append(r)
        # эпизоды входа
        ep, last_t0 = 0, None
        for r in taken:
            if last_t0 is None or (r["t0"] - last_t0) > a.gap_min * 60 * NS:
                ep += 1
            r["episode"], last_t0 = ep, r["t0"]
        by_day = defaultdict(list)
        for r in taken:
            by_day[utc_day(r["t1"])].append(r)
        day_pnl = {d: sum(r["pnl"] for r in rs) for d, rs in by_day.items()}
        total = sum(day_pnl.values())
        losing = sorted((d for d, v in day_pnl.items() if v < 0), key=lambda d: day_pnl[d])
        print(f"\n######## {month}: {len(taken)} сд, {total:+.0f}$; дней в минусе {len(losing)} из {len(day_pnl)}, "
              f"их сумма {sum(day_pnl[d] for d in losing):+.0f}$, дней в плюсе {sum(1 for v in day_pnl.values() if v > 0)}"
              f" на {sum(v for v in day_pnl.values() if v > 0):+.0f}$")
        print("сутки UTC | $ | сделок +/− | стопов ($) | дедлайн ($) | трейл ($) | эпизодов | худший эпизод $ | BTC 4ч на входе (медиана)")
        for d in losing:
            rs = by_day[d]
            rsn = defaultdict(lambda: [0, 0.0])
            for r in rs:
                rsn[r["reason"]][0] += 1
                rsn[r["reason"]][1] += r["pnl"]
            eps = defaultdict(float)
            for r in rs:
                eps[r["episode"]] += r["pnl"]
            b4 = sorted(r["btc4h"] for r in rs if r["btc4h"] is not None)
            cell = lambda k: f"{rsn[k][0]} ({rsn[k][1]:+.0f})" if k in rsn else "0"
            b4_med = f"{b4[len(b4) // 2]:+.0f}" if b4 else "—"
            print(f"{d} | {day_pnl[d]:+.0f} | {len(rs)} +{sum(r['pnl'] > 0 for r in rs)}/−{sum(r['pnl'] <= 0 for r in rs)} | "
                  f"{cell('stop')} | {cell('deadline')} | {cell('trail')} | {len(eps)} | {min(eps.values()):+.0f} | {b4_med}")
        print("\nПодробно по каждому убыточному дню (эпизоды его сделок — целиком, с входами и в соседние сутки):")
        for d in losing:
            rs = by_day[d]
            print(f"\n== {d} ({month}): {day_pnl[d]:+.0f}$")
            for e in sorted({r["episode"] for r in rs}):
                er = [r for r in taken if r["episode"] == e]
                in_day = [r for r in er if utc_day(r["t1"]) == d]
                t0, t1 = min(r["t0"] for r in er), max(r["t1"] for r in er)
                b0 = btc.at(t0)
                print(f"   эпизод {local_hm(t0)}–{local_hm(max(r['t0'] for r in er))[6:]} GMT+4: {len(er)} входов, всего {sum(r['pnl'] for r in er):+.0f}$ "
                      f"(в этих сутках {len(in_day)} сд, {sum(r['pnl'] for r in in_day):+.0f}$); стопов {sum(r['reason'] == 'stop' for r in er)}; "
                      f"BTC 1ч/4ч на первом входе {fmt(er[0]['btc1h'])}/{fmt(er[0]['btc4h'])} bps; BTC от первого входа: минимум "
                      f"{fmt(bps(b0, btc.low(t0, t1)))} bps, к последнему выходу {fmt(bps(b0, btc.at(t1)))} bps")
            worst = sorted(rs, key=lambda r: r["pnl"])[:5]
            for r in worst:
                print(f"      {r['sym'][:-4]:<10} {local_hm(r['t0'])}→{local_hm(r['t1'])[6:]} {r['reason']:<8} {r['net']:+6.0f} bps {r['pnl']:+6.1f}$ "
                      f"(позиция ${r['usd']:.0f}); BTC за сделку {fmt(r['btc_move'])} bps, худшее {fmt(r['btc_low'])}")
        for r in taken:
            csv_rows.append({"month": month, "exit_day": utc_day(r["t1"]), "episode": r["episode"], "symbol": r["sym"],
                             "t0_ns": r["t0"], "t1_ns": r["t1"],
                             "t0_local": local_hm(r["t0"]), "t1_local": local_hm(r["t1"]), "reason": r["reason"],
                             "net_bps": round(r["net"], 2), "usd": round(r["usd"], 2), "pnl_usd": round(r["pnl"], 2),
                             "btc1h_bps": r["btc1h"], "btc4h_bps": r["btc4h"],
                             "btc_move_bps": None if r["btc_move"] is None else round(r["btc_move"], 1),
                             "btc_low_bps": None if r["btc_low"] is None else round(r["btc_low"], 1)})
    if a.csv and csv_rows:
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(csv_rows[0]))
            w.writeheader()
            w.writerows(csv_rows)


if __name__ == "__main__":
    main()
