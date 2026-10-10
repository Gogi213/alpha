#!/usr/bin/env python3
"""П-09 §7 п. 2 — ворота до чтения на сутках (по умолчанию 03.08, дом e-aug). Печать — только статусы и счёт выходов.

(б) базы этого прогона = прежние: `p09-b-base` = `p07b-base`, `p09-m-base` = `p07m-main-julgate` (тела rounds+signals
    без строк `#`, без колонки `form`);
(в) по ключу сигнала (symbol, day_utc, signal_index), до `busy-replay`:
    - вход (t0_ns, entry_px, qty, fill_frac, entry_vwap, legs_*) у каждой клетки = базе;
    - сделка клетки без выхода `wall_eat_*` = базе целиком;
    - `a` = та из `m`/`l`, что вышла раньше (время, причина, цена, bps), и `exit_ns(a) = min(m, l)`;
    - при одном режиме `exit_ns(X=20) ≤ exit_ns(X=50)`.

    python3 p09-gate.py [--home ~/alpha/epochs/e-aug] [--day 2026-08-03]
"""
import csv
import os
import sys

A = os.path.expanduser("~/alpha")
SET = "t-bid-btc4h-q1"
ENTRY = ("t0_ns", "entry_px", "qty", "fill_frac", "entry_vwap", "legs_filled", "legs_rejected")
EXIT = ("exit_px", "net_bps", "reason", "exit_ns")


def arg(name, default):
    a = sys.argv[1:]
    return a[a.index(name) + 1] if name in a else default


HOME = os.path.expanduser(arg("--home", f"{A}/epochs/e-aug"))
DAY = arg("--day", "2026-08-03")


def path(cell, f):
    return f"{HOME}/b5/{cell}/{DAY}/{SET}/{f}"


def body(cell, f):
    """Строки без `#`, колонка `form` (3-я у rounds/signals) выброшена — у клеток она разная по имени."""
    out = []
    for l in open(path(cell, f), encoding="utf-8").read().splitlines():
        if l.startswith("#"):
            continue
        parts = l.split(",")
        out.append(",".join(parts[:2] + parts[3:]) if f == "rounds.csv" else l)
    return out


def rows(cell):
    with open(path(cell, "rounds.csv"), encoding="utf-8") as fh:
        r = csv.DictReader(l for l in fh if not l.startswith("#"))
        return {(x["symbol"], x["day_utc"], x["signal_index"]): x for x in r}


def main():
    bad = []
    for new, old in (("p09-b-base", "p07b-base"), ("p09-m-base", "p07m-main-julgate")):
        if not os.path.exists(path(old, "rounds.csv")):
            print(f"(б) {new}: эталона {old} нет — не сверено")
            continue
        for f in ("rounds.csv", "signals.csv"):
            ok = body(new, f) == body(old, f)
            print(f"(б) {new} {f} = {old}: {'ok' if ok else 'РАЗНИЦА'}")
            ok or bad.append(f"б {new} {f}")
    for tag in "bm":
        base = rows(f"p09-{tag}-base")
        cells = {(mode, x): rows(f"p09-{tag}-{mode}{x}") for mode in "mla" for x in (50, 20)}
        for (mode, x), c in cells.items():
            n_eat = sum(r["reason"].startswith("wall_eat_") for r in c.values())
            e = [k for k in base if k not in c or any(c[k][f] != base[k][f] for f in ENTRY)]
            e += [k for k in c if k not in base]
            e += [k for k, r in c.items() if k in base and not r["reason"].startswith("wall_eat_")
                  and any(r[f] != base[k][f] for f in EXIT)]
            print(f"(в) p09-{tag}-{mode}{x}: выходов wall_eat_* {n_eat}; вход/без съедания = база: "
                  f"{'ok' if not e else f'РАЗНИЦА {len(e)}'}")
            e and bad.append(f"в {tag}{mode}{x} база")
        for x in (50, 20):
            a, m, l = cells[("a", x)], cells[("m", x)], cells[("l", x)]
            e = []
            for k, r in a.items():
                first = min((m[k], l[k]), key=lambda z: int(z["exit_ns"]))
                if any(r[f] != first[f] for f in EXIT):
                    e.append(k)
            print(f"(в) p09-{tag}-a{x} = min(m, l): {'ok' if not e else f'РАЗНИЦА {len(e)}'}")
            e and bad.append(f"в {tag}a{x} min")
        for mode in "mla":
            c20, c50 = cells[(mode, 20)], cells[(mode, 50)]
            e = [k for k in c20 if int(c20[k]["exit_ns"]) > int(c50[k]["exit_ns"])]
            print(f"(в) p09-{tag}-{mode}: X20 не позже X50: {'ok' if not e else f'РАЗНИЦА {len(e)}'}")
            e and bad.append(f"в {tag}{mode} X")
    print("ВОРОТА П-09:", "ok" if not bad else "НЕ ПРОЙДЕНЫ " + "; ".join(bad))
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
