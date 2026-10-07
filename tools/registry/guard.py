#!/usr/bin/env python3
"""Защита от повторов (TK-081, В-196): реестр спрашивается до счёта и замера. Работает на сервере счёта рядом с snap.py.
  guard.py check <класс> [--recompute --why ТЕКСТ] [--repeat N] -- <команда…>
      rc 0 — считать (stdout: возможно, команда с урезанным --cells: строка `GUARD-CELLS <файл>`),
      rc 3 — всё уже посчитано / замер уже был: «взято из реестра», ссылка на готовый результат,
      rc 2 — отказ (--recompute без --why).
  guard.py done <класс> <rc> [--result ПУТЬ] -- <команда…>   — после конца: запись в журнал отпечатков
Журнал — $REG_DIR/ledger.jsonl (дописывается; канон реестра docs/registry подтягивает его import-auto).
Отпечаток = команда + env + sha скриптов и входных данных + md5 бинарников + git-коммит: «старое» за новое не выдаётся.
Клетка (--cells <файл>, строка «форма набор») имеет свой отпечаток: общий отпечаток без списка клеток + строка + тело её --set."""
import argparse, hashlib, json, os, re, shlex, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snap

SKIP, REFUSE = 3, 2
VOLATILE_ENV = ("BENCH_FROZEN", "BENCH_IOSTATE")


def ledger_path():
    return os.path.join(os.environ.get("REG_DIR", snap.REG), "ledger.jsonl")


def read_ledger():
    p = ledger_path()
    if not os.path.exists(p):
        return []
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]


def append(row):
    os.makedirs(os.path.dirname(ledger_path()), exist_ok=True)
    with open(ledger_path(), "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _h(o):
    return hashlib.sha256(json.dumps(o, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]


def split_cells_args(argv):
    """argv без `--cells X` и без `--set …` (их учитывают клетки) -> (остаток, путь списка клеток | None, тело --set по имени)."""
    out, cells, sets, i = [], None, {}, 0
    while i < len(argv):
        a = argv[i]
        if a == "--cells" and i + 1 < len(argv):
            cells = argv[i + 1]; i += 2; continue
        if a.startswith("--cells="):
            cells = a[8:]; i += 1; continue
        if a == "--set" and i + 1 < len(argv):
            n, _, body = argv[i + 1].partition(":"); sets[n] = body; i += 2; continue
        out.append(a); i += 1
    return out, cells, sets


def context(argv):
    """Общая часть отпечатка: всё, что влияет на результат, кроме списка клеток."""
    files, inputs, bins = snap.collect(argv)
    rest, cells, sets = split_cells_args(argv)
    env = {k: v for k, v in sorted(os.environ.items()) if snap.ENVRE.match(k) and k not in VOLATILE_ENV}
    code = {"bins": {p: b["md5"] for p, b in sorted(bins.items())}}
    data = {p: (v.get("sha256") or [v.get("size"), v.get("mtime")]) for p, v in sorted(inputs.items())}
    scripts = {p: v["sha256"] for p, v in sorted(files.items()) if p != cells}
    return {"fp": _h({"argv": rest, "env": env, "code": code, "data": data, "scripts": scripts}),
            "code": _h(code), "data": _h(data), "cells": cells, "sets": sets}


def cell_fps(ctx):
    if not ctx["cells"] or not os.path.isfile(ctx["cells"]):
        return {}
    out = {}
    for ln in open(ctx["cells"], encoding="utf-8"):
        p = ln.split()
        if len(p) == 2:
            out[ln.strip()] = _h({"ctx": ctx["fp"], "line": ln.strip(), "set": ctx["sets"].get(p[1], "")})
    return out


def check(cls, argv, recompute=False, why="", repeat=0, say=print):
    if recompute and not why.strip():
        say("guard: --recompute требует --why «причина» (напр. «изменился код») — отказ")
        return REFUSE, None
    ctx = context(argv)
    led = read_ledger()
    done_runs = [r for r in led if r.get("kind") == "run" and r.get("fp") == ctx["fp"] and r.get("rc") == 0]
    if recompute:
        append({"kind": "recompute", "ts": snap.time.strftime("%Y-%m-%dT%H:%M:%S%z"), "fp": ctx["fp"], "cls": cls, "why": why,
                "cmd": shlex.join(argv)[:400]})
        return 0, None
    if cls in ("wave", "stand", "measure"):
        n = len(done_runs)
        if n and not (repeat and n < repeat):
            r = done_runs[-1]
            say(f"guard: замер с тем же отпечатком {ctx['fp']} уже есть ({n} раз): {r.get('result_path') or r.get('ts')} — "
                f"отказ; шум проверять флагом --repeat N, иное — --recompute --why")
            return SKIP, r
        return 0, None
    cfp = cell_fps(ctx)
    if cfp:
        got = {r["cell_fp"]: r for r in led if r.get("kind") == "cell" and r.get("cell_fp") in set(cfp.values())}
        missing = [ln for ln, f in cfp.items() if f not in got]
        if not missing:
            say(f"guard: взято из реестра — все {len(cfp)} клеток уже посчитаны (отпечаток {ctx['fp']}); считать нечего")
            for f in list(got)[:3]:
                say(f"  результат: {got[f].get('result_path')}")
            return SKIP, list(got.values())[-1]
        if got:
            part = ctx["cells"] + f".todo-{ctx['fp']}"
            open(part, "w", encoding="utf-8", newline="\n").write("\n".join(missing) + "\n")
            say(f"guard: взято из реестра {len(got)} клеток из {len(cfp)}; считается {len(missing)} недостающих")
            say(f"GUARD-CELLS {part}")
            return 0, {"cells": part}
        return 0, None
    if done_runs:
        r = done_runs[-1]
        say(f"guard: взято из реестра — этот запуск уже посчитан (отпечаток {ctx['fp']}): {r.get('result_path') or r.get('ts')}")
        return SKIP, r
    return 0, None


def done(cls, rc, argv, result=None, wall_s=None, say=print):
    ctx = context(argv)
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    append({"kind": "run", "ts": ts, "fp": ctx["fp"], "code": ctx["code"], "data": ctx["data"], "cls": cls, "rc": int(rc),
            "result_path": result, "wall_s": wall_s, "cmd": shlex.join(argv)[:400]})
    if int(rc) == 0:
        for ln, f in cell_fps(ctx).items():
            append({"kind": "cell", "ts": ts, "cell_fp": f, "cell": ln, "run_fp": ctx["fp"], "result_path": result})


def main():
    a = sys.argv[1:]
    if "--" not in a:
        sys.exit(__doc__)
    sep = a.index("--")
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["check", "done"])
    p.add_argument("cls")
    p.add_argument("rc", nargs="?", default="0")
    p.add_argument("--recompute", action="store_true")
    p.add_argument("--why", default="")
    p.add_argument("--repeat", type=int, default=0)
    p.add_argument("--result")
    o = p.parse_args(a[:sep])
    argv = a[sep + 1:]
    if o.cmd == "check":
        rc, _ = check(o.cls, argv, o.recompute, o.why, o.repeat)
        sys.exit(rc)
    done(o.cls, o.rc, argv, o.result)


if __name__ == "__main__":
    main()
