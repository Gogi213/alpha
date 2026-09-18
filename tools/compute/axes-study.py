#!/usr/bin/env python3
"""E2 базы отскока (В-64): оси практиков В-44 на касаниях с порогом В-61.

Все девять осей T37 были измерены при заглушке `k = 1.0` (любое место стакана —
шум). Здесь то же самое на касаниях с силой «×соседи» ≥ S % и номиналом ≥ N $:
по каждой корзине оси — число касаний, доля «уровень выжил» (`ended_by_death =
false`) и средний markout «в сторону отскока» на 60 с / 10 мин / 1 ч (bps, из
`m_60s`/`m_10m`/`m_1h`; горизонт внутри касания — `within_touch_60s` — пропускается
для 60 с). База для сравнения — строка «все».

Оси (корзины — границы В-44 из `lob::touch_axes`, здесь повторены буквально):
  возраст          [0,10 мин) / [10 мин, 1 ч) / [1 ч, ∞)
  круглость        0 / 1 / ≥2 нулей
  фронтран         0 / (0, 0.5) / [0.5, ∞) от размера
  номер касания    1 / 2–3 / ≥4
  подход 1 с       <0 / [0,1) / [1,2.5) / [2.5,∞) bps (плюс — к уровню)
  длительность     <1 с / [1,10) / [10,60) / ≥60 с
  стек             1 / 2–3 / ≥4 живых ≥ H3 в 25 bps (включая сам уровень)
  сторона          bid / ask
  час UTC          0–5 / 6–11 / 12–17 / 18–23
  σ_60 (bps)       квартили по выборке (вол. сейчас — E12)

Использование: axes-study.py <каталог touches> <instruments.csv> [S%] [N$]
"""
import csv
import os
import sys
from collections import defaultdict


def read_ticks(path):
    ticks = {}
    with open(path, encoding="utf-8") as f:
        for row in csv.DictReader(l for l in f if not l.startswith("#")):
            sym = row.get("symbol", "")
            if sym and not sym.startswith("#"):
                try:
                    ticks[sym] = float(row["tick_size"])
                except (KeyError, ValueError):
                    pass
    return ticks


def fnum(row, key):
    v = row.get(key, "")
    if v == "":
        return None
    try:
        return float(v)
    except ValueError:
        return None


def bucket_age(ms):
    return "<10м" if ms < 600_000 else ("10м–1ч" if ms < 3_600_000 else "≥1ч")


def bucket_round(z):
    return "0" if z == 0 else ("1" if z == 1 else "≥2")


def bucket_frontrun(lots, size):
    if size <= 0:
        return None
    share = lots / size
    return "0" if share <= 0 else ("(0,0.5)" if share < 0.5 else "≥0.5")


def bucket_touch_index(i):
    return "1" if i == 0 else ("2–3" if i <= 2 else "≥4")


def bucket_approach(a):
    if a is None:
        return None
    return "<0" if a < 0 else ("[0,1)" if a < 1 else ("[1,2.5)" if a < 2.5 else "≥2.5"))


def bucket_duration(ms):
    return "<1с" if ms < 1_000 else ("1–10с" if ms < 10_000 else ("10–60с" if ms < 60_000 else "≥60с"))


def bucket_stack(n):
    return "1" if n <= 1 else ("2–3" if n <= 3 else "≥4")


def bucket_hour(start_ms):
    h = (start_ms // 1000 % 86_400) // 3_600
    return f"{(h // 6) * 6:02d}–{(h // 6) * 6 + 5:02d}"


def main():
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(2)
    d, inst = sys.argv[1], sys.argv[2]
    s_min = float(sys.argv[3]) if len(sys.argv) > 3 else 300.0
    n_min = float(sys.argv[4]) if len(sys.argv) > 4 else 1000.0
    ticks = read_ticks(inst)
    rows = []  # (axes dict, survived, m60 or None, m10m, m1h, sigma60)
    coins = 0
    for name in sorted(os.listdir(d)):
        if not (name.startswith("touches-") and name.endswith(".csv")):
            continue
        sym = name[len("touches-"):-len(".csv")]
        tick = ticks.get(sym)
        if tick is None:
            continue
        coins += 1
        with open(os.path.join(d, name), encoding="utf-8") as f:
            for r in csv.DictReader(l for l in f if not l.startswith("#")):
                strength = fnum(r, "strength_w20_pct")
                size = fnum(r, "size_at_touch") or 0.0
                price_tick = fnum(r, "price_tick") or 0.0
                if strength is None or strength < s_min or size * price_tick * tick < n_min:
                    continue
                age = fnum(r, "age_ms") or 0.0
                axes = {
                    "сторона": r.get("side", ""),
                    "возраст": bucket_age(age),
                    "круглость": bucket_round(int(fnum(r, "round_zeros") or 0)),
                    "фронтран": bucket_frontrun(fnum(r, "frontrun_lots") or 0.0, size),
                    "номер касания": bucket_touch_index(int(fnum(r, "touch_index") or 0)),
                    "подход 1с": bucket_approach(fnum(r, "approach_1s")),
                    "длительность": bucket_duration(fnum(r, "duration_ms") or 0.0),
                    "стек": bucket_stack(int(fnum(r, "stack_levels") or 1)),
                    "час UTC": bucket_hour(int(fnum(r, "start_ms") or 0)),
                }
                survived = r.get("ended_by_death") != "true"
                m60 = None if r.get("within_touch_60s") == "true" else fnum(r, "m_60s")
                rows.append((axes, survived, m60, fnum(r, "m_10m"), fnum(r, "m_1h"), fnum(r, "sigma_60s_bps")))
    n_all = len(rows)
    print(f"coins={coins} касаний после фильтра (сила ≥ {s_min:g} %, номинал ≥ ${n_min:g}) = {n_all}")
    # σ_60 квартили → корзины
    sig = sorted(x[5] for x in rows if x[5] is not None)
    if sig:
        q1, q2, q3 = (sig[int(0.25 * (len(sig) - 1))], sig[int(0.5 * (len(sig) - 1))], sig[int(0.75 * (len(sig) - 1))])
        for x in rows:
            s = x[5]
            x[0]["σ_60 bps"] = None if s is None else (f"<{q1:.0f}" if s < q1 else (f"[{q1:.0f},{q2:.0f})" if s < q2 else (f"[{q2:.0f},{q3:.0f})" if s < q3 else f"≥{q3:.0f}")))

    def stats(sel):
        n = len(sel)
        surv = sum(1 for x in sel if x[1]) / n if n else float("nan")
        def mean(i):
            xs = [x[i] for x in sel if x[i] is not None]
            return (sum(xs) / len(xs), len(xs)) if xs else (float("nan"), 0)
        return n, surv, mean(2), mean(3), mean(4)

    def line(label, sel):
        n, surv, (m60, n60), (m10, _), (m1h, _) = stats(sel)
        return f"  {label:14s} n={n:6d}  выжил={surv:5.1%}  m60={m60:+6.1f} (n={n60})  m10м={m10:+6.1f}  m1ч={m1h:+7.1f}"

    print(line("все", rows))
    order = ["возраст", "круглость", "фронтран", "номер касания", "подход 1с", "длительность", "стек", "сторона", "час UTC", "σ_60 bps"]
    for axis in order:
        by = defaultdict(list)
        for x in rows:
            b = x[0].get(axis)
            if b is not None:
                by[b].append(x)
        print(f"== {axis}")
        for b in sorted(by, key=lambda k: (len(k), k)):
            print(line(b, by[b]))
    print()
    print("Чтение: «выжил» — уровень пережил касание; m — средний markout в сторону отскока, bps (≈ 0 — ось не двигает цену). База — строка «все».")


if __name__ == "__main__":
    main()
