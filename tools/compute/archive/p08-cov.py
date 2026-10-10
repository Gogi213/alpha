#!/usr/bin/env python3
"""П-08 §12 п. 9, 17: охват всех 14 клеток по сигналам B1 авг + сен БЕЗ исхода и заморозка порогов (TK-013).

  feats   — признаки Rust-клеток (g36/g55/g140/g07) по `b5/p08-b1` всех домов авг/сен → `tmp-p08/cov/{aug,sep}-<sub>`
            (Python-клетки g57/g78/g126 там уже есть — `bin/p08-cov.sh`, п. 12).
  decide  — f_m (доля сигналов B1 месяца, которую клетка оставляет), ожидаемо сделок f × 424 / f × 141 и механические
            правила: п. 9 (< 30 сделок хоть в одном месяце — «мёртвый» порог) и п. 17 (> 90 % сигналов хоть в одном
            месяце — «зеркальное») → квантиль признака по сигналам B1 АВГУСТА по направлению гипотезы: основная
            оставляет треть (ранг ⌈n/3⌉), сосед — две трети (⌈2n/3⌉). Особые: Г-36 — срез ≤ 0 → «> 0», клетки
            совпадают → одна; Г-55 — доля «eat > 0» < 1/3 → одна клетка «eat > 0 и BTC ≥ −5 → пропуск». Правило
            применяется один раз, без повторов. → `tmp-p08/cov/p08-coverage.json` (в репо — `docs/findings/`).
  keep    — keep-файлы всех 14 (итоговых) клеток по замороженному JSON → `tmp-p08/cov2/{aug,sep}-<sub>` (для
            `p08-run.py cells --cov tmp-p08/cov2`).

На деке: python3 bin/p08-cov.py feats && python3 bin/p08-cov.py decide && python3 bin/p08-cov.py keep
"""
import csv
import importlib.util
import json
import math
import os
import subprocess
import sys

A = os.path.expanduser("~/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))
COV = f"{A}/tmp-p08/cov"
COV2 = f"{A}/tmp-p08/cov2"
FEATS = os.path.join(HERE, "p08-feats.py")
T = {"aug": 424, "sep": 141}  # сделок B1 в месяце (§12 п. 9)
HOMES = {"aug": [(f"{A}/epochs/e-aug", range(1, 32), "2026-08")],
         "sep": [(f"{A}/tmp-lsk0914.used-20260926/home", range(1, 16), "2026-09"),
                 (f"{A}/tmp-t29/rec", range(16, 24), "2026-09")]}
PREV_DAY = {"aug": "2026-07-31", "sep": "2026-08-31"}  # история 60 мин Г-140 через полночь
# Python-клетки — те же источники, что `bin/p08-cov.sh` (ворота (г) зелёные на них)
PY_SRC = {"aug": ["--src", "epochs/e-aug/b5/p07b-base", "--home", "epochs/e-aug", "--home", "."],
          "sep": ["--src", "tmp-lsk0914.used-20260926/home/b5/p07b-base", "--src", "tmp-t29/rec/b5/p07b-base",
                  "--home", "tmp-lsk0914.used-20260926/home", "--home", "tmp-t29/rec", "--home", "epochs/e-archive",
                  "--home", "."]}
RUST = ("g36", "g55", "g140", "g07")
PY = ("g57", "g78", "g126")
# Исходные пороги §7/§12 (те же, что CELLS в p08-feats.py; сверка — в decide): имя → (признак, оп, порог)
ORIG = {
    "g57": [("p08-g57-rv", "coin_rv_1h", ">", 46), ("p08-g57-rng", "coin_range_1h", ">", 54)],
    "g78": [("p08-g78-q33", "coin_minus_btc_15m", "<=", -13.134246), ("p08-g78-25", "coin_minus_btc_15m", "<=", -25)],
    "g126": [("p08-g126-5", "n_other_signal_15m", "<", 5), ("p08-g126-10", "n_other_signal_15m", "<", 10)],
    "g36": [("p08-g36-a", "g36_ratio", ">=", 1.0), ("p08-g36-b", "g36_ratio", ">=", 0.46)],
    "g55": [("p08-g55-20", "g55_eat_share", "skip>=", 0.20), ("p08-g55-50", "g55_eat_share", "skip>=", 0.50)],
    "g140": [("p08-g140-30", "g140_pool_share", "<", 0.30), ("p08-g140-50", "g140_pool_share", "<", 0.50)],
    "g07": [("p08-g07-up", "g07_vs_up", ">=", 0), ("p08-g07-mid", "g07_vs_mid", ">=", 0)],
}
LOW_KEEPS = {"g78": True, "g126": True, "g140": True, "g57": False, "g36": False}  # направление гипотезы (§2)


def load_feats_mod():
    spec = importlib.util.spec_from_file_location("p08f", FEATS)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def run(args, log):
    with open(log, "w", encoding="utf-8") as fh:
        r = subprocess.run(["nice", "-n", "19", sys.executable, FEATS] + args, cwd=A, stdout=fh, stderr=subprocess.STDOUT)
    if r.returncode:
        sys.exit(f"упало: {' '.join(args)} (лог {log})")


def rust_src(m):
    out = []
    for home, days, ym in HOMES[m]:
        for d in days:
            if not os.path.exists(f"{home}/b5/p08-b1/{ym}-{d:02d}/.done"):
                sys.exit(f"нет {home}/b5/p08-b1/{ym}-{d:02d}/.done — прогон П-08 не закончен")
        out += ["--src", f"{home}/b5/p08-b1", "--home", home]
    if m == "sep":
        out += ["--home", f"{A}/epochs/e-archive"]
    return out + ["--home", A]


def mf_dir(m):
    """Каталог-ссылки на минутные ряды месяца (+ прошлые сутки) — intensity_feats читает всё под каталогом."""
    d = f"{A}/tmp-p08/mf-{m}"
    os.makedirs(d, exist_ok=True)
    days = [PREV_DAY[m]] + [f"{ym}-{x:02d}" for _, xs, ym in HOMES[m] for x in xs]
    for day in days:
        src, dst = f"{A}/tmp-p08/mf/{day}", os.path.join(d, day)
        if os.path.isdir(src) and not os.path.lexists(dst):
            os.symlink(src, dst)
    return d


def cmd_feats(out, cov_json=None, subs=RUST):
    for m in ("aug", "sep"):  # август первым: g07 замораживает терцили по августу
        for sub in subs:
            args = [sub] + (PY_SRC[m] if sub in PY else rust_src(m)) + ["--out", f"{out}/{m}-{sub}"]
            if sub == "g140":
                args += ["--intensity", mf_dir(m)]
            if sub == "g07":
                args += ["--terc-json", f"{COV}/g07-terc.json"]
            if cov_json:
                args += ["--cov-json", cov_json]
            run(args, f"{out}/{m}-{sub}.log")
            print(f"{m} {sub}: готово")


def read_feats(m, sub):
    with open(f"{COV}/{m}-{sub}/feats-{sub}.csv", encoding="utf-8", newline="") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for k, v in list(r.items()):
            if k.startswith(("coin_", "n_other", "g")) and not k.startswith("p08"):
                r[k] = None if v in ("", None) else float(v)
    return rows


def passes(r, feat, op, thr, fm):
    if op.startswith("skip"):  # Г-55: вход, если не «съедание и BTC стоит»
        return bool(fm.g55_cond(r, thr, strict=op == "skip>"))
    v = r.get(feat)
    return v is not None and fm.OPS[op](thr)(v)


def share(rows, feat, op, thr, fm):
    return sum(passes(r, feat, op, thr, fm) for r in rows) / len(rows) if rows else 0.0


def quantile_cut(vals, keep, low):
    vs = sorted(vals) if low else sorted(vals, reverse=True)
    return vs[math.ceil(keep * len(vs)) - 1]


def cmd_decide():
    fm = load_feats_mod()
    data = {m: {s: read_feats(m, s) for s in PY + RUST} for m in ("aug", "sep")}
    n = {m: len(data[m]["g57"]) for m in data}
    cells, final = [], {}
    for sub, orig in ORIG.items():
        # сверка: флаги feats-файла = исходное правило (одно определение с p08-feats.py)
        for name, feat, op, thr in orig:
            for m in data:
                bad = sum(int(r[name]) != passes(r, feat, op, thr, fm) for r in data[m][sub] if name in r)
                if bad:
                    sys.exit(f"{m} {name}: {bad} расхождений флага с исходным правилом")
        final[sub] = []
        for role, (name, feat, op, thr) in zip(("основная", "сосед"), orig):
            f = {m: share(data[m][sub], feat, op, thr, fm) for m in data}
            exp = {m: round(f[m] * T[m], 1) for m in data}
            dead, mirror = any(exp[m] < 30 for m in data), any(f[m] > 0.9 for m in data)
            c = {"cell": name, "sub": sub, "role": role, "feature": feat, "op": op, "thr": thr,
                 "f": {m: round(f[m], 4) for m in data}, "expected_trades": exp, "dead_lt30": dead, "mirror_gt90": mirror}
            new = (name, feat, op, thr)
            if dead or mirror:
                keep = 1 / 3 if role == "основная" else 2 / 3
                aug_vals = [r[feat] for r in data["aug"][sub] if r.get(feat) is not None]
                if sub == "g55":
                    pos = sum(v > 0 for v in aug_vals) / len(aug_vals)
                    c["aug_share_eat_gt0"] = round(pos, 4)
                    if pos >= 1 / 3:
                        sys.exit("g55: доля eat > 0 ≥ 1/3 — ветка квантилей доли съедания не предусмотрена, к Судье")
                    new = ("p08-g55-any", feat, "skip>", 0.0)
                elif sub == "g07":  # клетка уже — терциль августа (своей монеты); замена не предусмотрена
                    c["judge_pending"] = "терцильная клетка сработала по п. 9/17 — оставлена как есть до ответа Судьи"
                else:
                    low = LOW_KEEPS[sub]
                    cut = quantile_cut(aug_vals, keep, low)
                    c["aug_rank"] = math.ceil(keep * len(aug_vals))
                    if sub == "g36" and cut <= 0:
                        new = ("p08-g36-pos", feat, ">", 0.0)
                    else:
                        new = (f"p08-{sub}-q{'33' if keep < 0.5 else '67'}", feat, "<=" if low else ">=", cut)
                c["decision"] = "к Судье (без изменений)" if "judge_pending" in c else "заменена"
            else:
                c["decision"] = "без изменений"
            nn, nf, no, nt = new
            if any(x["name"] == nn for x in final[sub]):
                c["decision"] += f" — совпала с {nn}, одна клетка (−1 испытание)"
            else:
                final[sub].append({"name": nn, "feature": nf, "op": no, "thr": nt})
            c["final"] = {"name": nn, "op": no, "thr": nt,
                          "f": {m: round(share(data[m][sub], nf, no, nt, fm), 4) for m in data}}
            c["final"]["expected_trades"] = {m: round(c["final"]["f"][m] * T[m], 1) for m in data}
            c["final"]["lt30_label"] = any(v < 30 for v in c["final"]["expected_trades"].values())
            # Судья 04:38: замена одна и последняя; > 90 % после неё — ярлык «почти не действует» (описание)
            c["final"]["gt90_label"] = any(v > 0.9 for v in c["final"]["f"].values())
            if sub == "g55":
                c["final"]["changed_share"] = {m: round(1 - c["final"]["f"][m], 4) for m in data}
            cells.append(c)
    extra = {"g140_abs_ge_030_share": {m: round(sum((r.get("g140_pool_share") or 0) >= 0.30 for r in data[m]["g140"])
                                                / len(data[m]["g140"]), 4) for m in data}}
    out = {"protocol": "docs/research/P-08-new-code-7.md §12 п. 9, 17 (Судья 7ee1483, 128d8f6); исход не читался",
           "signals_b1": n, "b1_trades": T, "cells": cells, "final": final,
           "m_family": sum(len(v) for v in final.values()), "extra": extra}
    path = f"{COV}/p08-coverage.json"
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(out, fh, ensure_ascii=False, indent=1)
    for c in cells:
        print(f"{c['cell']:14} f {c['f']['aug']:.3f}/{c['f']['sep']:.3f} → {c['decision']:40} {c['final']['name']} "
              f"{c['final']['op']} {c['final']['thr']:.6g} f {c['final']['f']['aug']:.3f}/{c['final']['f']['sep']:.3f}")
    print(f"m = {out['m_family']}; {path}")


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "feats":
        cmd_feats(COV)
    elif cmd == "decide":
        cmd_decide()
    elif cmd == "keep":
        os.makedirs(COV2, exist_ok=True)
        cmd_feats(COV2, f"{COV}/p08-coverage.json", PY + RUST)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
