#!/usr/bin/env python3
"""Контроль «рост рынка» для сделок сетки (аудит дизайна 22.09 §4 С1, §6 п. 1; В-85 п. 2).

Вердикт сравнивает форму с нулём, а на выборке из растущих суток любой лонг на час-два в плюсе.
Контроль отвечает на вопрос «что дала бы та же позиция в ту же сторону на той же монете в тот же
день на том же удержании, но в случайную минуту»: для каждой сделки берётся средний ход середины
монеты за `h` минут по **всем** минутам её суток (`study/touches/<сутки>/mids1m-<SYM>.csv`,
`h` — удержание сделки от сигнала до выхода, округлённое до минуты), со знаком стороны, минус
издержки круга с выходом по рынку `ROUNDTRIP_FEES_BPS = 4.41` (В-63). Превышение = `net_bps`
сделки − контроль. Будущее контроль не видит лишь в смысле отбора: это безусловный дрейф дня,
а не прогноз, — он отделяет бету дня от преимущества сигнала.

    python3 bin/placebo.py --grid-dir b5/<метка>[/<набор>] [--form <имя>] [--mids study/touches] \
        [--csv study/placebo-<метка>.csv]

Печать — по форме: сделок, среднее `net_bps`, контроль, превышение (среднее и медиана), в скольких
сутках превышение > 0. `--form` не задан — все формы сетки.
"""
from __future__ import annotations

import argparse
import csv
import os
import statistics as st
import sys
from collections import defaultdict

for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass

# Издержки круга мейкер-вход + выход по рынку, bps (В-63; `costs.rs` ROUNDTRIP_FEES_BPS).
ROUNDTRIP_FEES_BPS = 4.41


def load_rounds(path: str, form: str | None) -> list[dict]:
    with open(path, encoding="utf-8", errors="replace", newline="") as f:
        body = [l for l in f if not l.startswith("#")]
    out = []
    for r in csv.DictReader(body):
        if form and r["form"] != form:
            continue
        try:
            out.append({
                "symbol": r["symbol"], "day": r["day_utc"], "form": r["form"],
                "dir": int(r["dir"]), "net_bps": float(r["net_bps"]),
                "t0_ns": int(r["t0_ns"]), "exit_ns": int(r["exit_ns"]) if r.get("exit_ns") else None,
            })
        except (KeyError, ValueError):
            continue
    return out


class Mids:
    """Минутные середины монеты-суток и средний ход за h минут по всем минутам суток (кэш)."""

    def __init__(self, base: str):
        self.base = base
        self._mids: dict[tuple[str, str], dict[int, int]] = {}
        self._drift: dict[tuple[str, str, int], float | None] = {}

    def mids(self, day: str, sym: str) -> dict[int, int]:
        k = (day, sym)
        if k not in self._mids:
            m: dict[int, int] = {}
            p = os.path.join(self.base, day, f"mids1m-{sym}.csv")
            if os.path.exists(p):
                with open(p, encoding="utf-8", newline="") as f:
                    for r in csv.DictReader(f):
                        m[int(r["minute_ms"])] = int(r["mid2x"])
            self._mids[k] = m
        return self._mids[k]

    def drift_bps(self, day: str, sym: str, h_min: int) -> float | None:
        k = (day, sym, h_min)
        if k not in self._drift:
            m = self.mids(day, sym)
            moves = [
                (m[t + h_min * 60_000] / m[t] - 1.0) * 1e4
                for t in m
                if m[t] > 0 and (t + h_min * 60_000) in m
            ]
            self._drift[k] = st.mean(moves) if moves else None
        return self._drift[k]


def control(rows: list[dict], mids: Mids) -> list[dict]:
    out = []
    for r in rows:
        if r["exit_ns"] is None:
            continue
        h = max(1, round((r["exit_ns"] - r["t0_ns"]) / 60e9))
        d = mids.drift_bps(r["day"], r["symbol"], h)
        if d is None:
            continue
        ctrl = r["dir"] * d - ROUNDTRIP_FEES_BPS
        out.append({**r, "h_min": h, "control_bps": ctrl, "excess_bps": r["net_bps"] - ctrl})
    return out


def summary(rows: list[dict]) -> dict:
    by_day: dict[str, list[float]] = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r["excess_bps"])
    return {
        "n": len(rows),
        "net_bps": st.mean(r["net_bps"] for r in rows),
        "control_bps": st.mean(r["control_bps"] for r in rows),
        "excess_bps": st.mean(r["excess_bps"] for r in rows),
        "excess_median_bps": st.median(r["excess_bps"] for r in rows),
        "days": len(by_day),
        "days_excess_pos": sum(1 for v in by_day.values() if st.mean(v) > 0),
        "by_day": {d: st.mean(v) for d, v in sorted(by_day.items())},
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--grid-dir", required=True)
    ap.add_argument("--form", default=None)
    ap.add_argument("--mids", default="study/touches", help="каталог кэша касаний с mids1m-<SYM>.csv по суткам")
    ap.add_argument("--csv", default=None, help="сводка по формам в CSV")
    ap.add_argument("--min-n", type=int, default=1, help="печатать формы с числом сделок не меньше")
    a = ap.parse_args(argv)
    rows = load_rounds(os.path.join(a.grid_dir, "rounds.csv"), a.form)
    if not rows:
        print(f"placebo: нет сделок в {a.grid_dir}" + (f" формы {a.form}" if a.form else ""))
        return 1
    mids = Mids(a.mids)
    by_form: dict[str, list[dict]] = defaultdict(list)
    for r in control(rows, mids):
        by_form[r["form"]].append(r)
    lost = len(rows) - sum(len(v) for v in by_form.values())
    sums = {f: summary(v) for f, v in by_form.items() if len(v) >= a.min_n}
    order = sorted(sums, key=lambda f: -sums[f]["excess_bps"])
    print(f"placebo {a.grid_dir}: форм {len(sums)}, сделок без минутных середин {lost} (не в счёт); "
          f"контроль = ход той же монеты в тот же день на том же удержании в случайную минуту − {ROUNDTRIP_FEES_BPS} bps")
    for f in order:
        s = sums[f]
        print(f"  {f}: n={s['n']}, net {s['net_bps']:+.2f}, контроль {s['control_bps']:+.2f}, "
              f"превышение {s['excess_bps']:+.2f} (медиана {s['excess_median_bps']:+.2f}) bps, "
              f"> 0 в {s['days_excess_pos']} сутках из {s['days']}")
    if a.csv:
        with open(a.csv, "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["grid", "form", "n", "net_bps", "control_bps", "excess_bps", "excess_median_bps",
                        "days", "days_excess_pos"])
            for f in order:
                s = sums[f]
                w.writerow([a.grid_dir, f, s["n"], f"{s['net_bps']:.4f}", f"{s['control_bps']:.4f}",
                            f"{s['excess_bps']:.4f}", f"{s['excess_median_bps']:.4f}", s["days"], s["days_excess_pos"]])
    return 0


if __name__ == "__main__":
    sys.exit(main())
