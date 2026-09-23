#!/usr/bin/env python3
"""G11 (В-88): прокиды через стоп и выключатель по BTC на обвале — по минутным свечам Bybit.

Бэктест кандидата видит только те сделки, что случились; здесь — «что было бы» с позицией, открытой
в худшую минуту, и с выключателем, закрывающим всё по ходу BTC за 1 ч. Свечи — `ref-klines.py`
(`ref-<SYM>-1m.csv`), исполнение приближённое: стоп — по минимуму (и по закрытию) минуты, в которой
минимум впервые прошёл стоп; выход по выключателю — по закрытию минуты после срабатывания.

    python3 crash-gaps.py --klines epochs/e-crash/study/klines --btc epochs/e-crash/study/regime/ref-BTCUSDT-1m.csv \\
        --entry-from 2025-10-10T20:30 --entry-to 2025-10-10T21:30 --hold-from 2025-10-10T20:50 \\
        --stop-pct 2 --kill-bps 100,150,200,300,500 [--calm-btc <ref-BTCUSDT-1m.csv> …]
"""
import argparse
import csv
import datetime as dt
import glob
import os
import statistics as st

MIN = 60_000
HOUR = 3_600_000


def load(path):
    return {int(r["minute_ms"]): (float(r["low"]), float(r["close"])) for r in csv.DictReader(open(path, encoding="utf-8"))}


def ms(s):
    return int(dt.datetime.fromisoformat(s).replace(tzinfo=dt.timezone.utc).timestamp() * 1000)


def hm(t):
    return dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%H:%M")


def stop_hit(k, t_entry, stop, horizon_min):
    """(минимум, закрытие) минуты, где минимум впервые прошёл стоп, относительно входа по закрытию."""
    e = k[t_entry][1]
    for t in range(t_entry + MIN, t_entry + (horizon_min + 1) * MIN, MIN):
        if t in k and k[t][0] <= e * (1 - stop):
            return k[t][0] / e - 1, k[t][1] / e - 1, t
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--klines", required=True)
    ap.add_argument("--btc", required=True)
    ap.add_argument("--entry-from", required=True)
    ap.add_argument("--entry-to", required=True)
    ap.add_argument("--hold-from", required=True, help="вход позиции для выключателя (закрытие этой минуты)")
    ap.add_argument("--stop-pct", type=float, default=2.0)
    ap.add_argument("--hold-min", type=int, default=240, help="удержание, мин (кандидат — 4 ч)")
    ap.add_argument("--kill-bps", default="100,150,200,300,500")
    ap.add_argument("--calm-btc", action="append", default=[], help="свечи BTC спокойных эпох — сколько раз сработал бы выключатель")
    a = ap.parse_args()
    stop = a.stop_pct / 100
    coins = {os.path.basename(f)[4:-7]: load(f) for f in sorted(glob.glob(os.path.join(a.klines, "ref-*-1m.csv")))}
    t0, t1, th = ms(a.entry_from), ms(a.entry_to), ms(a.hold_from)

    worst = []
    for sym, k in coins.items():
        w = None
        for t in range(t0, t1 + MIN, MIN):
            if t not in k:
                continue
            h = stop_hit(k, t, stop, a.hold_min)
            if h and (w is None or h[0] < w[0]):
                w = (h[0], h[1], t, h[2])
        if w:
            worst.append((sym, *w))
    lo = sorted(x[1] * 100 for x in worst)
    cl = sorted(x[2] * 100 for x in worst)
    print(f"монет {len(coins)}; вход по закрытию минуты {a.entry_from}…{a.entry_to}, стоп −{a.stop_pct:g} %:")
    print(f"  худший исход по монете (минимум минуты стопа): медиана {st.median(lo):.1f} %, четверть худших ≤ "
          f"{lo[len(lo) // 4]:.1f} %, худший {lo[0]:.1f} %; по закрытию: медиана {st.median(cl):.1f} %, худший {cl[0]:.1f} %")
    for sym, l, c, te, ts in sorted(worst, key=lambda x: x[1])[:8]:
        print(f"    {sym:<14} вход {hm(te)} стоп {hm(ts)}: минимум {l * 100:.1f} %, закрытие {c * 100:.1f} %")

    btc = load(a.btc)
    print(f"выключатель «BTC за 1 ч ≤ −K» для позиции с {hm(th)} (выход по закрытию следующей минуты):")
    for kb in [int(x) for x in a.kill_bps.split(",")]:
        trig = next((t for t in range(th, th + a.hold_min * MIN, MIN)
                     if t in btc and t - HOUR in btc and btc[t][1] / btc[t - HOUR][1] - 1 <= -kb / 1e4), None)
        if trig is None:
            print(f"  −{kb / 100:.1f} %: не сработал")
            continue
        out = [(k[trig + MIN][1] / k[th][1] - 1) * 100 for k in coins.values() if th in k and trig + MIN in k]
        bottom = [(min(k[t][0] for t in range(th + MIN, th + a.hold_min * MIN, MIN) if t in k) / k[th][1] - 1) * 100
                  for k in coins.values() if th in k]
        print(f"  −{kb / 100:.1f} %: сработал {hm(trig)}; закрыто: медиана {st.median(out):.1f} %, худшая {min(out):.1f} %; "
              f"без выключателя дно за удержание: медиана {st.median(bottom):.1f} %")

    for path in a.calm_btc:
        k = sorted((t, v[1]) for t, v in load(path).items())
        c = dict(k)
        for kb in [int(x) for x in a.kill_bps.split(",")]:
            ep, last, n = 0, None, 0
            for t, x in k:
                p = c.get(t - HOUR)
                if p and x / p - 1 <= -kb / 1e4:
                    n += 1
                    if last is None or t - last > HOUR:
                        ep += 1
                    last = t
            span = f"{dt.datetime.fromtimestamp(k[0][0] / 1000, dt.timezone.utc):%Y-%m-%d}…{dt.datetime.fromtimestamp(k[-1][0] / 1000, dt.timezone.utc):%Y-%m-%d}"
            print(f"  спокойные {span}: BTC 1 ч ≤ −{kb / 100:.1f} % — эпизодов {ep}, минут {n}")


if __name__ == "__main__":
    main()
