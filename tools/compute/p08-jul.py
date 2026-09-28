#!/usr/bin/env python3
"""П-08 — июль (§9 п. 4, §10.3; §12 п. 15, 18; Судья 12:30 `97a6823`; TK-013).

Клетки — все 12 итоговых из JSON охвата (пороги заморожены по августу, терцили Г-07 — `tmp-p08/cov/g07-terc.json`);
8 отобранных по В-146 (`tmp-p08/p08-read.json` → `to_july`) читаются по §10.3, 4 прочих — «описание, не отобран»
(TK-018), вне Холма и вердикта. Путь — как авг/сен: `p08-feats.py --cov-json` на сигналах B1 июля
(`epochs/e-jul/b5/p08-b1`, 31 сутки `.same` с `p07b-base` TK-010) → `busy-replay --keep` → `portfolio-sim` (эпоха
`июль`, $2500/$500, DROP TRX+KORU как TK-010; потолок 3 у Г-126 и B2). KPI и состояние — функциями `p07-jul-read.py`
(июльские границы месяца, `verdict_one_month`, `paired_b2`), Δ$ — `p07-read.daily_series/block_boot` по суткам июля.

Ворота до чтения: B1 = «оставить всё» тем же путём = закрытия `tmp-p07/jul-read/b-base` (TK-010) байт в байт;
красное — стоп без чисел. В stdout — только статусы и путь; числа — в `tmp-p08/p08-jul.json`.

С1 доля клетки ≤ доля базы − 0,10; С2 для каждых суток d: доля клетки без d ≤ доля базы без d − 0,10; С3 верх 95 % Δ$ ≥ 0.
Все три — «подтверждено на июле»; С1 нет и (авг или сен: доля клетки > доля базы − 0,10) — «стоп»; иначе — «не ясно».

    python3 bin/p08-jul.py > tmp-p08/p08-jul.log
"""
import csv
import importlib.util
import json
import os
import subprocess
import sys

A = os.path.expanduser("~/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(A, "tmp-p08", "jul")
COVJ = os.path.join(A, "tmp-p08", "cov", "p08-coverage.json")
READ = os.path.join(A, "tmp-p08", "p08-read.json")
FEATS = os.path.join(HERE, "p08-feats.py")
JUL_HOME = os.path.join(A, "epochs", "e-jul")
SRC = os.path.join(JUL_HOME, "b5", "p08-b1")
FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
MARGIN = 0.10


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


JR = load(os.path.join(HERE, "p07-jul-read.py"), "jr")
PR = JR.load_mod("p07-read.py", "p07read")
T9 = load(os.path.join(HERE, "p07-tk009-read.py"), "t9")


def feats(sub, out):
    args = [sys.executable, FEATS, sub, "--src", SRC, "--home", JUL_HOME, "--out", out, "--cov-json", COVJ]
    if sub == "g140":
        d = os.path.join(A, "tmp-p08", "mf-jul")
        os.makedirs(d, exist_ok=True)
        for day in JR.JUL_DAYS:
            s, t = os.path.join(A, "tmp-p08", "mf", day), os.path.join(d, day)
            if os.path.isdir(s) and not os.path.lexists(t):
                os.symlink(s, t)
        args += ["--intensity", d]
    if sub == "g07":
        args += ["--terc-json", os.path.join(A, "tmp-p08", "cov", "g07-terc.json")]
    with open(out + ".log", "w", encoding="utf-8") as fh:
        if subprocess.run(["nice", "-n", "10"] + args, cwd=A, stdout=fh, stderr=subprocess.STDOUT).returncode:
            sys.exit(f"упало: p08-feats {sub} (лог {out}.log)")


def run_cell(name, keep_path, cap):
    d = os.path.join(OUT, name)
    out_dir = os.path.join(d, "jul")
    os.makedirs(out_dir, exist_ok=True)
    PR.run(["python3", PR.BUSY_REPLAY, SRC, out_dir, "--sets", JR.SET_, "--keep", keep_path])
    co = os.path.join(d, "ps-closes.json")
    PR.run(["python3", PR.PORT_SIM, "--epoch", f"июль={out_dir}:.", "--variant", f"cell={JR.SET_}/{FORM}",
            "--klines", os.path.join(JUL_HOME, "study/klines"), "--klines", os.path.join(A, "study/klines"),
            "--deposit-usd", "2500", "--position-usd", "500", "--max-pos", str(cap),
            "--day-stop-pct", "0", "--btc-kill-bps", "0", "--drop", JR.DROP,
            "--json", os.path.join(d, "ps.json"), "--closes-out", co])
    c = json.load(open(co, encoding="utf-8"))
    return sorted(tuple(x) for x in c.get("cell", {}).get("июль", {}).get(str(cap), [])), \
        PR.symbol_map({"jul": out_dir}, FORM)


def main():
    cov = json.load(open(COVJ, encoding="utf-8"))
    rd = json.load(open(READ, encoding="utf-8"))
    sel, augsep = set(rd["to_july"]), rd["cells"]
    fd = os.path.join(OUT, "feats")
    os.makedirs(fd, exist_ok=True)
    for sub in cov["final"]:
        feats(sub, os.path.join(fd, sub))
    # все сигналы B1 июля (без TRX) — keep «оставить всё»
    rows = list(csv.DictReader(open(os.path.join(fd, "g57", "feats-g57.csv"), encoding="utf-8")))
    allk = os.path.join(OUT, "keepall.csv")
    with open(allk, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["symbol", "t0_ns", "price_tick", "form"])
        w.writerows(sorted({(r["symbol"], r["t0_ns"], r["price_tick"], r["form"]) for r in rows}))
    kn = PR.load_mod(PR.KN_PATH, "kn")
    orig_ms = JR.patch_july(kn)
    b1, s1 = run_cell("B1", allk, 0)
    ref = json.load(open(os.path.join(A, "tmp-p07/jul-read/b-base/ps-closes.json"), encoding="utf-8"))
    ref = sorted(tuple(x) for x in ref.get("cell", {}).get("июль", {}).get("0", []))
    gate = b1 == ref
    print(f"ворота: B1 июля «оставить всё» против tmp-p07/jul-read/b-base: {'ЗЕЛЁНОЕ' if gate else 'КРАСНОЕ — стоп'}")
    if not gate:
        sys.exit(1)
    b2, s2 = run_cell("B2", allk, 3)
    bases = {"B1": (b1, s1), "B2": (b2, s2)}
    res = {"B1": {"n": len(b1), "usd": round(sum(p for _, p in b1), 2), **JR.verdict_one_month(PR, kn, b1, s1)},
           "B2": {"n": len(b2), "usd": round(sum(p for _, p in b2), 2), **JR.verdict_one_month(PR, kn, b2, s2)}}
    for sub, cells in cov["final"].items():
        for c in cells:
            bk = "B2" if sub == "g126" else "B1"
            cl, sm = run_cell(c["name"], os.path.join(fd, sub, f"keep-{c['name']}.csv"), 3 if bk == "B2" else 0)
            bc, _ = bases[bk]
            r = {"base": bk, "selected": c["name"] in sel, "n": len(cl), "usd": round(sum(p for _, p in cl), 2),
                 **JR.verdict_one_month(PR, kn, cl, sm)}
            uc, _ = PR.daily_series(cl, JR.JUL_DAYS)
            ub, _ = PR.daily_series(bc, JR.JUL_DAYS)
            units = [x - y for x, y in zip(uc, ub)]
            r["delta_usd"] = PR.block_boot(units)
            r["p"] = round(T9.boot_p(units), 5)
            fb = res[bk]["frac_gt_5d"]
            r["C1"] = r["frac_gt_5d"] is not None and fb is not None and r["frac_gt_5d"] <= round(fb - MARGIN, 3) + 1e-9
            r["C2"] = all(x["ok"] for x in JR.paired_b2(kn, orig_ms, cl, bc))
            r["C3"] = r["delta_usd"]["ci95"][1] >= 0
            if r["selected"]:
                a = augsep[c["name"]]
                bb = rd["bases"][bk]["frac"]
                not_better = any(a["frac"][pk] is None or a["frac"][pk] > bb[pk] - MARGIN for pk in ("aug", "sep"))
                r["verdict"] = ("подтверждено на июле" if r["C1"] and r["C2"] and r["C3"]
                                else "стоп" if (not r["C1"] and not_better) else "не ясно")
            else:
                r["verdict"] = "описание, не отобран (TK-018)"
            res[c["name"]] = r
    m = len(sel)
    ps = sorted((res[k]["p"], k) for k in sel)
    stop = False
    for i, (p, k) in enumerate(ps):
        ok = (not stop) and p <= 0.05 / (m - i)
        stop = stop or not ok
        res[k]["holm_sig"] = ok
    out = {"protocol": "docs/research/P-08-new-code-7.md §10.3; Судья 97a6823", "m_holm": m, "gate_b1": gate,
           "cells": res}
    path = os.path.join(A, "tmp-p08", "p08-jul.json")
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("готово:", path)


if __name__ == "__main__":
    main()
