#!/usr/bin/env python3
"""TK-315 (П-17 F2): канонические хеши состава F2 из tk314-f2-grid (колонка rec_le100) — те же функции, что p17_scope (F1).
Г-136/Г-137 — хеш набора; Г-81/Г-101/Г-135 — хеш варианта (семья + оси). Г-101 F2 — только стоп (part=stop; ladder в F2 не идёт).
    python tools/registry/p17_f2.py <grid.csv> [--out f2-cells.jsonl]    # печатает: строк, уникальных хешей, из них в F1 (scope.json), новых
Выход jsonl: hyp, axes, hash, new (не в F1), set (набор для Г-136/137), form (Г-135: postsweep<R>)."""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cellhash as ch  # noqa: E402
import master  # noqa: E402
import p17_scope as sc  # noqa: E402

COMMON = ["--entry-ttl-secs 1800", "--stop-form pct2", "--take-form tr1x1", "--deadline-secs 14400"]   # как L.args плана p17plan-b (форма B1)


def f2_cell(hyp, ax):
    """(хеш, доп. поля) клетки F2 по тем же правилам, что sc.cells() для F1."""
    if hyp == "Г-137":
        spec = f"{sc.SET_B1},repeat_min={int(ax['k'])}"
        return ch.cell_hash(sc.cfg_of(spec=spec)), {"set": spec}
    if hyp == "Г-136":
        spec = f"side=bid,btc4h_max=-44.55,agemax={int(ax['A_min']) * 60},repeat_min={int(ax['k'])}"
        return ch.cell_hash(sc.cfg_of(spec=spec)), {"set": spec}
    if hyp == "Г-101":
        a = {"part": "stop", "T_q": ax["T_q"], "f_bp": ax["f_bp"]}
        return sc.variant_hash("g101", a), {"axes_full": a}
    if hyp == "Г-81":
        return sc.variant_hash("g81", ax), {}
    if hyp == "Г-135":
        form = sc.BASE.replace("ladder3x0..0.0409sw2", str(ax["entry"]))
        return sc.variant_hash("g135", ax), {"form": form}
    raise SystemExit(f"неизвестная гипотеза {hyp}")


def build_plan(cells, plan_dir):
    """План-каталог для day-cells.sh (PLAN=L): L.cells/L.args — клетки-наборы (Г-136/137), L-<ГГГГ-ММ>.cells/.args — по порогу месяца
    (Г-81/101/135), строки и наборы — master.p17_cell (та же логика, что F1). Возвращает (хеш -> причина отказа) для клеток без строк."""
    os.makedirs(plan_dir, exist_ok=True)
    for x in cells:
        cfg = sc.cfg_of()
        av = dict(x.get("axes_full") or x["axes"])
        master.p17_cell(x["hyp"], av, cfg, x.get("form") or sc.BASE)
    skipped = {h: why for h, (ln, why) in master.LINES.items() if ln is None and h not in master.MONTHLY}
    base, months = {}, {}
    for h, (ln, _) in master.LINES.items():
        if ln:
            base[ln] = ln
    for h, mm in master.MONTHLY.items():
        for m, ln in mm.items():
            months.setdefault(m, set()).add(ln)

    def write(stem, lines):
        forms = sorted({ln.split()[0] for ln in lines})
        names = sorted({ln.split()[1] for ln in lines})
        ents = sorted({"--entry-form " + f.split("-pct2")[0] for f in forms})
        nl = chr(10)
        with open(os.path.join(plan_dir, stem + ".cells"), "w", encoding="utf-8", newline=nl) as f:
            f.write("".join(l + nl for l in sorted(lines)))
        with open(os.path.join(plan_dir, stem + ".args"), "w", encoding="utf-8", newline=nl) as f:
            f.write("".join(l + nl for l in ents + COMMON))
            f.write("".join(f"--set {n}:{master.MSETS[n]}" + nl for n in names if n in master.MSETS))
    write("L", set(base))
    for m, lines in sorted(months.items()):
        write("L-" + m, lines)
    return skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("grid")
    ap.add_argument("--out")
    ap.add_argument("--plan-dir", help="собрать план-каталог (нужны P-17-thresholds*.csv); печатает клетки без строк")
    a = ap.parse_args()
    f1 = {c["hash"] for cs in sc.cells().values() for c in cs}
    rows, seen = [], {}
    for r in csv.DictReader(open(a.grid, encoding="utf-8")):
        if r["rec_le100"] != "1":
            continue
        ax = json.loads(r["axes"])
        h, extra = f2_cell(r["hyp"], ax)
        rows.append({"hyp": r["hyp"], "axes": ax, "hash": h, "new": h not in f1, "control": r["control"] == "1", **extra})
        seen.setdefault(h, r["hyp"])
    uniq = {x["hash"]: x for x in rows}
    new = [x for x in uniq.values() if x["new"]]
    by = {}
    for x in new:
        by[x["hyp"]] = by.get(x["hyp"], 0) + 1
    print(json.dumps({"строк": len(rows), "уникальных": len(uniq), "в_F1": len(uniq) - len(new), "новых": len(new), "по_гипотезам": by}, ensure_ascii=False))
    if a.plan_dir:
        skipped = build_plan(list(uniq.values()), a.plan_dir)
        print(json.dumps({"без_строк": len(skipped), "причины": sorted(set(skipped.values()))[:6]}, ensure_ascii=False))
    if a.out:
        with open(a.out, "w", encoding="utf-8", newline="\n") as f:
            for x in uniq.values():
                f.write(json.dumps(x, ensure_ascii=False, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
