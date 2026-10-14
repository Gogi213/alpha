#!/usr/bin/env python3
"""Запуск бэктеста (TK-241, В-4..В-6): ядро для формы и CLI (CEO запускает тем же кодом).
  python tools/launch/launch.py plan   --hyps Г-04,Г-05 --months 2026-01,2026-02 [--axis keep_q=5,10] [--json]
  python tools/launch/launch.py submit --hyps … --months … [--axis …] [--dry-run] [--confirm-bin]
  python tools/launch/launch.py status
Дедуп — только по правилу Летописи (master.have_hashes: хеш есть = есть строки results по месяцу); свой хеш не изобретается
(tools/registry/cellhash.py). После постановки ничего не уходит нейросетям (В-6): ни тикетам, ни claude, ни Telegram."""
import argparse, collections, json, os, shlex, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REG = os.path.join(ROOT, "docs", "registry")
sys.path.insert(0, os.path.join(ROOT, "tools", "registry"))


def load_config(path=None):
    return json.load(open(path or os.path.join(HERE, "config.json"), encoding="utf-8"))


def plan_rows(protocol="P-14", fraction=1):
    """Клетки сетки (docs/registry/plan_cells.jsonl): hyp, hash, axes{}."""
    out = []
    for line in open(os.path.join(REG, "plan_cells.jsonl"), encoding="utf-8"):
        if line.strip():
            d = json.loads(line)
            if d["protocol"] == protocol and d["fraction"] == fraction:
                out.append({"hyp": d["hyp"], "hash": d["cell_hash"], "axes": json.loads(d["axes"])})
    return out


def plan(hyps, months, axes=None, rows=None, have=None, cfg=None):
    """Сухой прогон в единицах хеш×месяц (В-224). axes: {param: [значения]} — фильтр сетки (нет = все значения).
    -> {grid, have, new, hm_*, cells:[{hyp,hash,axes,have_months,new_months}], axis_cov:{param:{value:[посчитано, всего]}}}"""
    cfg = cfg or load_config()
    if rows is None:
        rows = plan_rows(cfg["protocol"], cfg["fraction"])
    if have is None:
        import master
        have = master.have_hashes()
    axes = {k: {str(x) for x in v} for k, v in (axes or {}).items() if v}
    months = list(months)
    cells, seen = [], set()
    cov = collections.defaultdict(lambda: collections.defaultdict(lambda: [0, 0]))
    for r in rows:
        if r["hyp"] not in hyps or any(str(r["axes"].get(k)) not in v for k, v in axes.items() if k in r["axes"]):
            continue
        got = have.get(r["hash"], set())
        hm = [m for m in months if m in got]
        for k, v in r["axes"].items():
            c = cov[k][str(v)]
            c[0] += len(hm)
            c[1] += len(months)
        if r["hash"] in seen:
            continue
        seen.add(r["hash"])
        cells.append({"hyp": r["hyp"], "hash": r["hash"], "axes": r["axes"], "have_months": hm, "new_months": [m for m in months if m not in got]})
    hm_have = sum(len(c["have_months"]) for c in cells)
    hm_new = sum(len(c["new_months"]) for c in cells)
    return {"grid": len(cells), "have": sum(1 for c in cells if not c["new_months"]), "new": sum(1 for c in cells if c["new_months"]),
            "hm_grid": len(cells) * len(months), "hm_have": hm_have, "hm_new": hm_new, "months": months, "cells": cells,
            "axis_cov": {k: dict(v) for k, v in cov.items()}}


def summary(p):
    return (f"в сетке {p['grid']} хешей × {len(p['months'])} мес. = {p['hm_grid']} хеш-мес.; в Летописи {p['hm_have']}; "
            f"к счёту {p['hm_new']} хеш-мес. ({p['new']} хешей)")


def bin_info(cfg):
    """Бинарь счёта (md5/коммит): из config.json; не задан — просит явного подтверждения."""
    return cfg.get("bin") or {"md5": None, "commit": None, "note": "не задан в config.json"}


def queue_cmd(cfg, run_id, plan_dir):
    q = cfg["queue"]
    return ["ssh", cfg["ssh_host"], "python3", cfg["alsched"], "submit", "--cls", q["cls"], "--name", run_id, "--cores", str(q["cores"]),
            "--mem", str(q["mem"]), "--max-runtime", str(q["max_runtime"]), "--est", str(q["est"]), "--", "env", f"P={plan_dir}",
            *shlex.split(cfg["runner"])]


def submit(hyps, months, axes=None, dry_run=False, confirm_bin=False, cfg=None, p=None, run_cmd=subprocess.run, log=print):
    """plan -> К счёту=0 отказ -> команда очереди calc; строка в docs/registry/runs.jsonl одинаково для формы и CLI."""
    cfg = cfg or load_config()
    p = p or plan(hyps, months, axes, cfg=cfg)
    if p["hm_new"] == 0:
        log("нечего считать: все выбранные хеш×месяц уже в Летописи")
        return {"ok": False, "why": "nothing-to-count", "plan": p}
    b = bin_info(cfg)
    if not dry_run and not confirm_bin:
        log(f"бинарь {b}: подтвердить явно (--confirm-bin), если он отличается от того, на котором посчитана Летопись (guard.equiv)")
        return {"ok": False, "why": "bin-not-confirmed", "plan": p}
    run_id = "L-" + time.strftime("%Y%m%d-%H%M%S")
    cmd = queue_cmd(cfg, run_id, f"/data/launch/{run_id}")
    log("команда: " + shlex.join(cmd))
    if dry_run:
        return {"ok": True, "dry_run": True, "run_id": run_id, "cmd": cmd, "plan": p}
    r = run_cmd(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"alsched submit rc={r.returncode}: {(r.stderr or r.stdout)[-300:]}")
        return {"ok": False, "why": "alsched-failed", "plan": p}
    row = {"id": run_id, "ts": time.strftime("%Y-%m-%dT%H:%M:%S+04:00"), "kind": "launch", "hyps": sorted(hyps), "months": list(months),
           "axes": axes or {}, "bin": b, "cmd": shlex.join(cmd), "hm_new": p["hm_new"], "source": "tools/launch/launch.py"}
    with open(os.path.join(REG, "runs.jsonl"), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")
    return {"ok": True, "run_id": run_id, "cmd": cmd, "plan": p}


def status(cfg=None, run_cmd=subprocess.run):
    cfg = cfg or load_config()
    r = run_cmd(["ssh", cfg["ssh_host"], "python3", cfg["alsched"], "ps"], capture_output=True, text=True)
    out = (r.stdout or r.stderr).strip()
    busy = sum(1 for l in out.splitlines() if "running" in l.lower())
    return {"text": out, "busy": busy, "warn": busy >= cfg.get("busy_warn_jobs", 1)}


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    sp = ap.add_subparsers(dest="cmd", required=True)
    for n in ("plan", "submit"):
        s = sp.add_parser(n)
        s.add_argument("--hyps", required=True)
        s.add_argument("--months", required=True)
        s.add_argument("--axis", action="append", default=[], help="param=v1,v2")
        s.add_argument("--json", action="store_true")
        if n == "submit":
            s.add_argument("--dry-run", action="store_true")
            s.add_argument("--confirm-bin", action="store_true")
    sp.add_parser("status")
    o = ap.parse_args(argv)
    if o.cmd == "status":
        s = status()
        print(s["text"])
        return 1 if s["warn"] else 0
    axes = {a.split("=", 1)[0]: a.split("=", 1)[1].split(",") for a in o.axis}
    hyps, months = [x for x in o.hyps.split(",") if x], [x for x in o.months.split(",") if x]
    if o.cmd == "plan":
        p = plan(hyps, months, axes)
        print(json.dumps({k: v for k, v in p.items() if k != "cells"}, ensure_ascii=False) if o.json else summary(p))
        return 0
    res = submit(hyps, months, axes, o.dry_run, o.confirm_bin)
    return 0 if res["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
