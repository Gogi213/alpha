#!/usr/bin/env python3
"""П-10 §7: охват клеток H–S и Г-89 по сигналам B1 авг + сен БЕЗ исхода, механическая замена и заморозка порогов (TK-024).

  feats   — признаки всех сигналов B1 (`p10-feats.py feats`) и сигналов клетки market (номинал стены D20) по месяцам
            → `tmp-p10/cov/{aug,sep}/{feats-p10.csv,g89-market.csv}`.
  decide  — f_m (доля сигналов, которую клетка оставляет), ожидаемо сделок f × 424 / f × 141, правило §7 (> 90 % сигналов
            или < 30 ожидаемых сделок хоть в одном месяце → квантиль по сигналам августа: основная ⌈n/3⌉, сосед ⌈2n/3⌉;
            клетки без квантиля (M, P, Q1–Q4, R, S, Г-89) — только ярлык) → ОДИН JSON `tmp-p10/cov/p10-coverage.json`.
            Исход читает только блок Г-120 (знак net_bps закрытий B1 — §14 п. 6): остальные признаки — без колонок исхода.
  keep    — keep-файлы итоговых клеток + `keepall`/`keepall-market` → `tmp-p10/cov/keep/keep-<клетка>.csv`
            (3 колонки symbol,t0_ns,price_tick — как `p08-run.py`).

На деке: python3 bin/p10-cov.py feats && python3 bin/p10-cov.py decide && python3 bin/p10-cov.py keep
"""
import csv
import importlib.util
import json
import os
import sys

A = os.path.expanduser("~/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))
COV = f"{A}/tmp-p10/cov"
MONTHS = ("aug", "sep")
LABEL_FEW, LABEL_NONE = "мало сделок", "почти не действует"
EXITSIM_FAMILIES = ["T:g24", "U:g25", "V:g109"]  # exit-sim (p10-exitsim.py): в m фиксированно, охват не нужен (§14 п. 3)
FAM_ORDER = ["H", "I", "J", "K", "L", "M", "N", "O", "P", "Q1", "Q2", "Q3", "Q4", "R", "S", "G89"]
HYP = {"H": "Г-11", "I": "Г-62", "J": "Г-74", "K": "Г-76", "L": "Г-22/Г-54", "M": "Г-123", "N": "Г-48/Г-49", "O": "Г-31",
       "P": "Г-47", "Q1": "Г-14", "Q2": "Г-14", "Q3": "Г-14", "Q4": "Г-14", "R": "Г-45", "S": "Г-120", "G89": "Г-89"}


def load_feats_mod():
    for d in (HERE, os.path.join(A, "bin")):
        p = os.path.join(d, "p10-feats.py")
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("p10f", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    sys.exit("нет p10-feats.py рядом и в ~/alpha/bin")


def read_market(path):
    with open(path, encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["g89_usd"] = None if r["g89_usd"] == "" else float(r["g89_usd"])
    return rows


def cmd_feats(fm):
    for m in MONTHS:  # сентябрь читает закрытия августа для семьи N — признаки каждого месяца считаются отдельно
        out = f"{COV}/{m}"
        os.makedirs(out, exist_ok=True)
        fm.cmd_feats(m, out)


def share(fm, op, thr, rows, feat):
    """Доля сигналов, которые клетка оставляет (нет значения — остаётся), и доля определённых."""
    if not rows:
        return 0.0, 0.0
    kept = sum(fm.keep_value(op, thr, r[feat]) for r in rows)
    defined = sum(r[feat] is not None for r in rows)
    return kept / len(rows), defined / len(rows)


def cmd_decide(fm):
    T = fm.p08c.T
    data = {m: fm.read_feats(f"{COV}/{m}/feats-p10.csv") for m in MONTHS}
    mkt = {m: read_market(f"{COV}/{m}/g89-market.csv") for m in MONTHS}
    s_keep, s_cnt = fm.s_block_keep(fm.chrono_day_dirs())  # знак net_bps закрытий B1 — только здесь (п. 14.6)
    n_sig = {m: len(data[m]) for m in MONTHS}
    s_kept = {m: sum(1 for k in s_keep if k[3].startswith("2026-08" if m == "aug" else "2026-09")) for m in MONTHS}
    cells, final, merged = [], {}, []
    for name, (fam, feat, kind, num, den) in fm.CELLS.items():
        op = fm.cell_op(name)
        rows = mkt if kind == "usd-top" else data
        if kind == "s-block":
            f = {m: s_kept[m] / n_sig[m] for m in MONTHS}
            dsh = {m: 1.0 for m in MONTHS}
            thr = None
        else:
            aug_vals = [r[feat] for r in data["aug"] if r[feat] is not None]
            thr = fm.cell_threshold(name, aug_vals)
            f, dsh = {}, {}
            for m in MONTHS:
                f[m], dsh[m] = share(fm, op, thr, rows[m], feat)
        exp = {m: round(f[m] * T[m], 1) for m in MONTHS}
        dead, mirror = any(exp[m] < 30 for m in MONTHS), any(f[m] > 0.9 for m in MONTHS)
        c = {"cell": name, "family": fam, "hypothesis": HYP[fam], "feature": feat, "kind": kind, "op": op, "thr": thr,
             "f": {m: round(f[m], 4) for m in MONTHS}, "defined_share": {m: round(dsh[m], 4) for m in MONTHS},
             "expected_trades": exp, "dead_lt30": dead, "mirror_gt90": mirror}
        if kind in ("low", "high", "skip-high"):
            c["decision"] = ("замена §7 по квантилю августа совпадает с самой клеткой — без изменений" if dead or mirror
                             else "без изменений")
        else:
            c["decision"] = "без замены (клетка без квантиля) — ярлык" if dead or mirror else "без изменений"
        c["label"] = LABEL_NONE if mirror else LABEL_FEW if dead else ""
        if kind == "usd-top":
            c["usd_min"] = thr
            c["usd_min_rule"] = "верхняя треть номинала стены по сигналам B1 августа (ранг ⌈n/3⌉ по убыванию), условие «≥»"
            c["usd_n_aug"] = len([r for r in data["aug"] if r["g89_usd"] is not None])
        if kind == "s-block":
            c["blocked_signals"] = {m: n_sig[m] - s_kept[m] for m in MONTHS}
            c["block_events_total"] = s_cnt["block_events"]
        key = (feat, op, thr)
        lst = final.setdefault(fam, [])
        dup = next((x for x in lst if (x["feature"], x["op"], x["thr"]) == key and kind not in ("s-block",)), None)
        if dup is not None:
            c["decision"] += f" — совпала с {dup['name']}, одна клетка (−1 семья)"
            merged.append({"family": fam, "cells": [dup["name"], name]})
        else:
            lst.append({"name": name, "feature": feat, "op": op, "thr": thr, "label": c["label"]})
        cells.append(c)
    fams = [x for x in FAM_ORDER if x in final]
    m_mine = len(fams) - len(merged)
    out = {"protocol": "docs/research/P-10-code-exists-batch.md §6.2, §7, §14 п. 6; признаки без колонок исхода "
                       "(исход читает только блок Г-120 — знак net_bps закрытий B1)",
           "signals_b1": n_sig, "signals_market": {m: len(mkt[m]) for m in MONTHS}, "b1_trades": T,
           "cells": cells, "final": final, "merged": merged,
           "families_p": fams, "families_exitsim": EXITSIM_FAMILIES,
           "m_family_p": m_mine, "m_family": m_mine + len(EXITSIM_FAMILIES),
           "m_family_without_merge": len(fams) + len(EXITSIM_FAMILIES),
           "cells_after": sum(len(v) for v in final.values()),
           "usd_min_g89": next(c["usd_min"] for c in cells if c["cell"] == "p10-g89-mkt-big")}
    os.makedirs(COV, exist_ok=True)
    path = f"{COV}/p10-coverage.json"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    for c in cells:
        print(f"{c['cell']:16} f {c['f']['aug']:.3f}/{c['f']['sep']:.3f} опред {c['defined_share']['aug']:.2f}/"
              f"{c['defined_share']['sep']:.2f} сделок {c['expected_trades']['aug']}/{c['expected_trades']['sep']} "
              f"{c['label'] or '-'} | {c['decision']}")
    print(f"m_family = {out['m_family']} (П {m_mine} + exit-sim 3; без слияний {out['m_family_without_merge']}); "
          f"клеток после замен {out['cells_after']}; {path}")


def write_keep3(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["symbol", "t0_ns", "price_tick"])
        w.writerows(sorted(set(rows)))


def cmd_keep(fm):
    j = json.load(open(f"{COV}/p10-coverage.json", encoding="utf-8"))
    data = {m: fm.read_feats(f"{COV}/{m}/feats-p10.csv") for m in MONTHS}
    mkt = {m: read_market(f"{COV}/{m}/g89-market.csv") for m in MONTHS}
    key = lambda r: (r["symbol"], r["t0_ns"], r["price_tick"])  # noqa: E731
    out = f"{COV}/keep"
    write_keep3(f"{out}/keep-p10-keepall.csv", [key(r) for m in MONTHS for r in data[m]])
    write_keep3(f"{out}/keep-p10-keepall-market.csv", [key(r) for m in MONTHS for r in mkt[m]])
    print(f"keepall: {sum(len(data[m]) for m in MONTHS)}; keepall-market: {sum(len(mkt[m]) for m in MONTHS)}")
    s_keep = None
    for fam, cl in j["final"].items():
        for c in cl:
            name = c["name"]
            kind = fm.CELLS[name][2]
            if kind == "s-block":
                if s_keep is None:
                    s_keep, _ = fm.s_block_keep(fm.chrono_day_dirs())
                rows = [k[:3] for k in s_keep]
            else:
                src = mkt if kind == "usd-top" else data
                rows = [key(r) for m in MONTHS for r in src[m] if fm.keep_value(c["op"], c["thr"], r[c["feature"]])]
            write_keep3(f"{out}/keep-{name}.csv", rows)
            print(f"{name}: оставлено {len(set(rows))}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    fm = load_feats_mod()
    if cmd == "feats":
        cmd_feats(fm)
    elif cmd == "decide":
        cmd_decide(fm)
    elif cmd == "keep":
        cmd_keep(fm)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
