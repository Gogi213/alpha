#!/usr/bin/env python3
"""TK-581: отчёт по выходу coverage.py. По эпизоду [с, по] (UTC, из netwatch) и бирже/типу записей:
  rx_факт     — записей, принятых за [с, по];
  E_факт      — записей, у которых время БИРЖИ E в [с, по] (пришли когда угодно до конца окна скана);
  фон         — записей/с: среднее по E-секундам [с−150, с−30] и [по+120+60, по+120+180] (без самих эпизодов — окно задаёт вызывающий, остальные эпизоды вычитаются из фона);
  модель      — ожидание = фон × длительность; охват E = E_факт / ожидание; недобор = max(0, ожидание − E_факт) — МОДЕЛЬ на допущении, что рынок в эпизод шёл как до и после.
python3 cov_report.py <cov-*.json ...> --ep HH:MM:SS-HH:MM:SS [--ep ...] [--ctl HH:MM:SS-HH:MM:SS ...]  (--ctl — окна без эпизодов, тот же расчёт: оценка шума модели)"""
import json, sys, collections, statistics as st

args = sys.argv[1:]
files = [a for a in args if a.endswith(".json")]
h = lambda s: int(s[:2]) * 3600 + int(s[3:5]) * 60 + int(s[6:])
eps, ctl = [], []
for i, a in enumerate(args):
    if a in ("--ep", "--ctl"):
        lo, hi = args[i + 1].split("-")
        (eps if a == "--ep" else ctl).append((h(lo), h(hi)))


def load(path):
    d = json.load(open(path))
    w0 = d["w0_ms"] // 1000
    day0 = w0 - (w0 % 86400)
    rx = collections.defaultdict(collections.Counter)
    e = collections.defaultdict(collections.Counter)
    holes = {}
    for f in d["files"]:
        for k, v in f["rx"].items():
            for s, c in v.items():
                rx[k][int(s) + w0 - day0] += c
        for k, v in f["e"].items():
            for s, c in v.items():
                e[k][int(s) + w0 - day0] += c
        for s, hs in f["holes"].items():
            holes.setdefault(s, []).extend(hs)
    return d["exchange"], rx, e, holes, d["w0_ms"], d["w1_ms"]


for p in files:
    ex, rx, e, holes, w0, w1 = load(p)
    day0 = (w0 // 1000) - ((w0 // 1000) % 86400)
    print(f"### {ex}")
    for tag, lo, hi in [("эпизод", l, u) for l, u in eps] + [("контроль", l, u) for l, u in ctl]:
        dur = hi - lo + 1
        row = []
        for k in ("b", "t", "m"):
            def rate(a, b, d):
                xs = [d[k][s] for s in range(a, b) if s not in excl]
                return st.mean(xs) if xs else 0.0
            # исключаем секунды всех заданных эпизодов (±60 с после, на догон) из фона
            excl = set()
            for l2, h2 in eps:
                excl.update(range(l2 - 10, h2 + 150))
            bg = (rate(lo - 150, lo - 30, e) + rate(hi + 150, hi + 400, e)) / 2 if k in e else 0
            rx_f = sum(rx[k][s] for s in range(lo, hi + 1)) if k in rx else 0
            e_f = sum(e[k][s] for s in range(lo, hi + 1)) if k in e else 0
            exp = bg * dur
            row.append((k, rx_f, e_f, exp))
        s_ = "  ".join(f"{k}: rx {rx_f:>8,} E {e_f:>8,} ожид {exp:>9,.0f} охват E {100*e_f/exp if exp else 0:5.1f}%".replace(",", " ") for k, rx_f, e_f, exp in row)
        print(f"  [{tag}] {lo//3600:02d}:{lo%3600//60:02d}:{lo%60:02d}–{hi//3600:02d}:{hi%3600//60:02d}:{hi%60:02d} ({dur} с)  {s_}")
    n_ids = sum(hi - lo + 1 for hs in holes.values() for lo, hi, _ in hs)
    print(f"  дыр в номерах сделок на конец окна: символов {len(holes)}, номеров {n_ids}")
