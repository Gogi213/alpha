#!/usr/bin/env python3
"""Сводка титрования F10 по корзинам режима и возрасту стены (план 2026-09-23, G4/G5).

Читает по каждой эпохе контроль «рост рынка» (`study/placebo-<тег>-<набор>.csv`, placebo.py) и итог
вердикта (`study/bounce-verdict-<тег>-<набор>.log`) и печатает рядом «историю» и «запись»: сделок,
net на сделку, контроль, превышение, суток с превышением > 0, деньги — по фактической позиции сделок прогона
(`_lib.fill_usd_by_form`, В-93; прежде — условная $1000, net × n / 10).
Корзина считается «держится», если знак net совпал в обеих эпохах и в каждой сделок не меньше --min-n
(это чтение, не вердикт: вердикт — только предрегистрированная форма, G6).

    titration-read.py --tag titr-<метка> --epoch история=<дом>/study --epoch запись=<дом>/study [--csv out]
"""
import argparse
import csv
import importlib.util
import os
import re

_spec = importlib.util.spec_from_file_location("_lib", os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lib.py"))
_lib = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_lib)


def read_epoch(study, tag):
    out = {}
    pref = f"placebo-{tag}-"
    for name in sorted(os.listdir(study)):
        if not (name.startswith(pref) and name.endswith(".csv")):
            continue
        set_name = name[len(pref):-4]
        with open(os.path.join(study, name), encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        if not rows:
            continue
        r = rows[0]
        verdict = ""
        vlog = os.path.join(study, f"bounce-verdict-{tag}-{set_name}.log")
        if os.path.exists(vlog):
            m = re.findall(r"ИТОГ=([^·]+)", open(vlog, encoding="utf-8", errors="replace").read())
            verdict = m[-1].strip() if m else ""
        n = int(r["n"])
        net = float(r["net_bps"])
        out[set_name] = {
            "n": n, "net": net, "control": float(r["control_bps"]), "excess": float(r["excess_bps"]),
            "days": int(r["days"]), "days_pos": int(r["days_excess_pos"]),
            "usd": sum(_lib.fill_usd_by_form(os.path.join(os.path.dirname(os.path.abspath(study)), "b5", tag), set_name).values())
            if os.path.isdir(os.path.join(os.path.dirname(os.path.abspath(study)), "b5", tag)) else None,
            "verdict": verdict,
        }
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tag", required=True)
    ap.add_argument("--epoch", action="append", required=True, help="имя=каталог study")
    ap.add_argument("--min-n", type=int, default=20)
    ap.add_argument("--csv", default=None)
    a = ap.parse_args()

    epochs = [e.split("=", 1) for e in a.epoch]
    data = {name: read_epoch(path, a.tag) for name, path in epochs}
    sets = sorted({s for d in data.values() for s in d})
    table = []
    for s in sets:
        row = {"set": s}
        signs = []
        for name, _ in epochs:
            v = data[name].get(s)
            for k in ("n", "net", "control", "excess", "days_pos", "days", "usd", "verdict"):
                row[f"{name}_{k}"] = "" if v is None else v[k]
            if v is not None and v["n"] >= a.min_n:
                signs.append(v["net"] > 0)
        row["holds"] = "да" if len(signs) == len(epochs) and len(set(signs)) == 1 else "нет"
        table.append(row)

    head = f"{'набор':<22}" + "".join(f" | {name:^38}" for name, _ in epochs) + " | держится"
    print(head)
    print(f"{'':<22}" + "".join(f" | {'сд.':>5} {'net':>6} {'конт':>6} {'прев':>6} {'дн+':>5} {'$':>6}" for _ in epochs))
    for row in table:
        line = f"{row['set']:<22}"
        for name, _ in epochs:
            if row[f"{name}_n"] == "":
                line += f" | {'—':^38}"
                continue
            line += (f" | {row[f'{name}_n']:>5} {row[f'{name}_net']:>+6.1f} {row[f'{name}_control']:>+6.1f} "
                     f"{row[f'{name}_excess']:>+6.1f} {row[f'{name}_days_pos']:>2}/{row[f'{name}_days']:<2} "
                     + (f"{row[f'{name}_usd']:>+6.0f}" if isinstance(row[f'{name}_usd'], (int, float)) else f"{'—':>6}"))
        print(line + f" | {row['holds']}")
    if a.csv:
        with open(a.csv, "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(table[0].keys()) if table else ["set"])
            w.writeheader()
            w.writerows(table)


if __name__ == "__main__":
    main()
