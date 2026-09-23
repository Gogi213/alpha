#!/usr/bin/env python3
"""Сводка титрования выхода лонга (план 2026-09-23, G9): по каждому набору и форме выхода — «история»
и «запись» рядом: сделок, net на сделку, контроль «рост рынка», превышение, суток с превышением > 0, $.

Читает `study/placebo-<тег>-<прогон>-<набор>.csv` (placebo.py по всем формам склейки) каждой эпохи.
Это чтение титрования, не вердикт: форма «держится», если знак net совпал в обеих эпохах при n ≥ --min-n.

    exit-titration-read.py --tag titrx-<метка> --epoch история=<дом>/study --epoch запись=<дом>/study [--csv out]
"""
import argparse
import csv
import os
import re

SETS = ["t-bid-btc1h-q1", "t-bid-btc4h-q1", "t-bid-age-45"]


def short_form(form):
    """`ladder3x2..20w2-pct1-tr1x0.5-3600-ttl1800-eat20` → `pct1 · tr1x0.5 · 1 ч · eat20`."""
    f = re.sub(r"^ladder[^-]*-", "", form).replace("-ttl1800", "")
    parts = f.split("-")
    out = []
    for p in parts:
        if p.isdigit():
            h = int(p) / 3600
            out.append(f"{h:g} ч")
        else:
            out.append(p)
    if not any(p.startswith("eat") or p.startswith("gone") for p in parts):
        out.append("без выхода по стене")
    return " · ".join(out)


def read_epoch(study, tag):
    rows = {}
    for name in os.listdir(study):
        m = re.match(rf"placebo-{re.escape(tag)}-(.+?)-(t-(?:bid|ask)-.+)\.csv$", name)
        if not m:
            continue
        set_name = m.group(2)
        with open(os.path.join(study, name), encoding="utf-8") as f:
            for r in csv.DictReader(f):
                n, net = int(r["n"]), float(r["net_bps"])
                rows[(set_name, r["form"])] = {
                    "n": n, "net": net, "control": float(r["control_bps"]), "excess": float(r["excess_bps"]),
                    "days_pos": int(r["days_excess_pos"]), "days": int(r["days"]), "usd": net * n / 10.0,
                }
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--epoch", action="append", required=True)
    ap.add_argument("--min-n", type=int, default=20)
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()
    epochs = [e.split("=", 1) for e in a.epoch]
    data = {name: read_epoch(path, a.tag) for name, path in epochs}
    keys = sorted({k for d in data.values() for k in d}, key=lambda k: (SETS.index(k[0]) if k[0] in SETS else 9, k[1]))
    table = []
    for set_name, form in keys:
        row = {"set": set_name, "form": form, "short": short_form(form)}
        signs = []
        for name, _ in epochs:
            v = data[name].get((set_name, form))
            for k in ("n", "net", "control", "excess", "days_pos", "days", "usd"):
                row[f"{name}_{k}"] = "" if v is None else v[k]
            if v is not None and v["n"] >= a.min_n:
                signs.append(v["net"] > 0)
        row["holds"] = "да" if len(signs) == len(epochs) and len(set(signs)) == 1 and signs[0] else "нет"
        table.append(row)
    current = None
    for row in table:
        if row["set"] != current:
            current = row["set"]
            print(f"\n== {current}")
            print(f"{'выход':<44}" + "".join(f" | {name:^32}" for name, _ in epochs) + " | плюс в обеих")
        line = f"{row['short']:<44}"
        for name, _ in epochs:
            if row[f"{name}_n"] == "":
                line += f" | {'—':^32}"
                continue
            line += (f" | {row[f'{name}_n']:>4} сд {row[f'{name}_net']:>+6.1f} bps {row[f'{name}_usd']:>+6.0f}$ "
                     f"прев {row[f'{name}_excess']:>+5.1f}")
        print(line + f" | {row['holds']}")
    if a.csv and table:
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0].keys()))
            w.writeheader()
            w.writerows(table)


if __name__ == "__main__":
    main()
