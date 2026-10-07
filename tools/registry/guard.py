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
import argparse, hashlib, json, os, re, shlex, shutil, sys, time

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


WRAP = {"benchrun-inner.sh": 2, "regrun.sh": 1, "benchrun.sh": 1, "benchrun2.sh": 1, "benchrun-sched.sh": 1}
DIRCAP = 5000
DERIVED = re.compile(r"\.abin(\.tmp\d*)?$")   # кэши счёта во входных каталогах (abin.rs/tbin.rs): версию данных несёт сам csv


def core_argv(argv):
    """Командная часть: обёртки (benchrun*, regrun, inner с envfile, alsched wave|stand) снимаются — check и done видят одно и то же."""
    a = list(argv)
    while a:
        i = 1 if os.path.basename(a[0]) in ("bash", "sh", "python3", "python") and len(a) > 1 else 0
        n = os.path.basename(a[i])
        if n in WRAP and len(a) > i + 1 + WRAP[n]:
            a = a[i + 1 + WRAP[n]:]
        elif n == "alsched.py" and len(a) > i + 1 and a[i + 1] in ("wave", "stand") and "--max-runtime" in a:
            a = a[a.index("--max-runtime") + 2:]
        else:
            break
    return a


def dir_fp(path):
    """Отпечаток каталога данных: имена, размеры и mtime на двух уровнях (не больше DIRCAP записей, срез помечается)."""
    h, n = hashlib.sha256(), 0
    stack = [(path, 0)]
    while stack and n < DIRCAP:
        d, lvl = stack.pop()
        try:
            ents = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        for e in ents:
            if DERIVED.search(e.name):
                continue
            n += 1
            if n >= DIRCAP:
                break
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:
                continue
            isdir = e.is_dir(follow_symlinks=False)
            if isdir and os.name == "nt":   # NTFS обновляет mtime каталога в родителе лениво — отпечаток мигал бы
                h.update(f"{d}/{e.name}/;".encode())
            else:
                h.update(f"{d}/{e.name}|{st.st_size}|{st.st_mtime_ns};".encode())
            if isdir and lvl < 1:
                stack.append((e.path, lvl + 1))
    return h.hexdigest()[:16] + ("+cap" if n >= DIRCAP else "")


DATA_ROOTS = ("/data/alpha/epochs", "/data/tk037/roots")
# входы bounce-grid (args.rs: root, touches_from, carry_root, sigma_from, regime_from, btc_minutes, verdict_csv) и touches (root, moves, numbers, minute_flow)
INPUT_FLAGS = ("--root", "--touches-from", "--carry-root", "--sigma-from", "--regime-from", "--btc-minutes", "--verdict-csv",
               "--touches-dir", "--moves", "--numbers", "--minute-flow")


def _under(p, base):
    return p == base or p.startswith(base.rstrip("/") + "/")


def _flag_values(argv, flag):
    vals = [argv[i + 1] for i in range(len(argv) - 1) if argv[i] == flag]
    return vals + [x[len(flag) + 1:] for x in argv if x.startswith(flag + "=")]


def _extra_runs_argvs(argv):
    """Строки --extra-runs <файл> несут свои входы и выходы: разбираются так же, как argv."""
    out = []
    for f in _flag_values(argv, "--extra-runs"):
        try:
            lines = open(f, encoding="utf-8").read().splitlines()
        except OSError:
            continue
        out += [shlex.split(ln.split("#", 1)[0], posix=os.name != "nt") for ln in lines if ln.split("#", 1)[0].strip()]
    return out


def input_dirs(texts, argv, out_dir):
    """Каталоги данных — только ВХОДЫ: значения входных флагов (и строк --extra-runs) и упомянутые пути внутри явных корней
    (GUARD_DATA_ROOTS). Выход (--out-dir и подкаталоги) и всё, что пишет задание, исключено — иначе прогон сам меняет свой отпечаток."""
    roots = [r for r in os.environ.get("GUARD_DATA_ROOTS", ":".join(DATA_ROOTS)).split(":") if r]
    runs = [argv] + _extra_runs_argvs(argv)
    cand = {v.rstrip("/") for r in runs for f in INPUT_FLAGS for v in _flag_values(r, f)}
    for t in texts:
        for m in snap.PATHRE.findall(t):
            m = m.rstrip("/")
            if any(_under(m, r) or _under(r, m) for r in roots):
                cand.add(m)
    outs = {o.rstrip("/") for r in runs for o in _flag_values(r, "--out-dir")} | ({out_dir.rstrip("/")} if out_dir else set())
    return sorted(d for d in cand if os.path.isdir(d) and not any(_under(d, o) or _under(o, d) for o in outs))


def _binaries(argv, env):
    bins = {}
    cands = [shutil.which(argv[0]) if argv else None, shutil.which("lob")] + [v for v in env.values() if os.path.isabs(v)]
    cands += [a for a in argv if os.path.isabs(a)]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK) and os.path.getsize(c) > snap.MAXCOPY:
            bins[c] = snap.sha(c, 5)
    return bins


def context(argv):
    """Общая часть отпечатка: всё, что влияет на результат, кроме списка клеток и выхода."""
    argv = core_argv(argv)
    env = {k: v for k, v in sorted(os.environ.items()) if snap.ENVRE.match(k) and k not in VOLATILE_ENV}
    files, inputs, bins = snap.collect(argv)
    bins = {**{p: b["md5"] for p, b in bins.items()}, **_binaries(argv, env)}
    texts = [" ".join(argv)] + [open(p, encoding="utf-8", errors="replace").read() for p in files if os.path.getsize(p) < 200_000]
    rest, cells, sets = split_cells_args(argv)
    out_dir = next((rest[i + 1] for i, x in enumerate(rest[:-1]) if x == "--out-dir"), None)
    rest = [x for i, x in enumerate(rest) if x != "--out-dir" and not (i and rest[i - 1] == "--out-dir")]
    dirs = {d: dir_fp(d) for d in input_dirs(texts, rest, out_dir)}
    code = {"bins": dict(sorted(bins.items()))}
    data = {p: (v.get("sha256") or [v.get("size"), v.get("mtime")]) for p, v in sorted(inputs.items())}
    data["dirs"] = dirs
    scripts = {p: v["sha256"] for p, v in sorted(files.items()) if p != cells}
    return {"fp": _h({"argv": rest, "env": env, "code": code, "data": data, "scripts": scripts}),
            "code": _h(code), "data": _h(data), "cells": cells, "sets": sets, "out_dir": out_dir, "argv": argv}


def cell_fps(ctx):
    if not ctx["cells"] or not os.path.isfile(ctx["cells"]):
        return {}
    out = {}
    for ln in open(ctx["cells"], encoding="utf-8"):
        p = ln.split()
        if len(p) == 2:
            out[ln.strip()] = _h({"ctx": ctx["fp"], "line": ln.strip(), "set": ctx["sets"].get(p[1], "")})
    return out


def _swap(argv, flag, old, new):
    return [new if (i and argv[i - 1] == flag and x == old) else x for i, x in enumerate(argv)]


def _pending(ctx, cls, calc_cells, out_dir, argv):
    d = os.path.join(os.environ.get("REG_DIR", snap.REG), "pending")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{ctx['fp']}-{int(time.time() * 1000)}.json")
    json.dump({"fp": ctx["fp"], "cls": cls, "cells": calc_cells, "out_dir": out_dir, "code": ctx["code"], "data": ctx["data"],
               "cmd": shlex.join(argv)[:400]}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return path


def check(cls, argv, recompute=False, why="", repeat=0, say=print):
    """-> (rc, info). info: fp, pending (путь записи ожидания для done), argv (команда для запуска: клетки/выход урезаны, если считается часть)."""
    if recompute and not why.strip():
        say("guard: --recompute требует --why «причина» (напр. «изменился код») — отказ")
        return REFUSE, None
    ctx = context(argv)
    led = read_ledger()
    done_runs = [r for r in led if r.get("kind") == "run" and r.get("fp") == ctx["fp"] and r.get("rc") == 0]
    run_argv = list(argv)
    calc, out_dir = {}, ctx["out_dir"]
    if recompute:
        append({"kind": "recompute", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "fp": ctx["fp"], "cls": cls, "why": why,
                "cmd": shlex.join(ctx["argv"])[:400]})
    elif cls in ("wave", "stand", "measure"):
        n = len(done_runs)
        if n and not (repeat and n < repeat):
            r = done_runs[-1]
            say(f"guard: замер с тем же отпечатком {ctx['fp']} уже есть ({n} раз): {r.get('result_path') or r.get('ts')} — "
                f"отказ; шум проверять флагом --repeat N, иное — --recompute --why")
            return SKIP, r
    else:
        cfp = cell_fps(ctx)
        if cfp:
            got = {r["cell_fp"]: r for r in led if r.get("kind") == "cell" and r.get("cell_fp") in set(cfp.values())}
            missing = {ln: f for ln, f in cfp.items() if f not in got}
            if not missing:
                say(f"guard: взято из реестра — все {len(cfp)} клеток уже посчитаны (отпечаток {ctx['fp']}); считать нечего")
                for f in list(got)[:3]:
                    say(f"  результат: {got[f].get('result_path')}")
                return SKIP, list(got.values())[-1]
            calc = missing
            if got:
                part = ctx["cells"] + f".todo-{ctx['fp']}"
                with open(part, "w", encoding="utf-8", newline="\n") as pf:
                    pf.write("\n".join(missing) + "\n")
                run_argv = _swap(run_argv, "--cells", ctx["cells"], part)
                if out_dir:   # урезанный счёт — в свой каталог: иначе перезапишет rounds.csv прежних клеток
                    out_dir = out_dir.rstrip("/") + f"-part-{ctx['fp'][:8]}"
                    run_argv = _swap(run_argv, "--out-dir", ctx["out_dir"], out_dir)
                else:
                    say("guard: ВНИМАНИЕ — --out-dir не найден в команде, выход урезанного счёта не разведён")
                say(f"guard: взято из реестра {len(got)} клеток из {len(cfp)}; считается {len(missing)} недостающих → {out_dir}")
        elif done_runs:
            r = done_runs[-1]
            say(f"guard: взято из реестра — этот запуск уже посчитан (отпечаток {ctx['fp']}): {r.get('result_path') or r.get('ts')}")
            return SKIP, r
    return 0, {"fp": ctx["fp"], "argv": run_argv, "pending": _pending(ctx, cls, calc, out_dir, argv)}


def done(cls, rc, argv=None, result=None, wall_s=None, pending=None, say=print):
    """Запись результата. С pending — ровно то, что проверял check (тот же отпечаток, клетки, выход); без него — по командной части argv."""
    ts = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    if pending:
        if not os.path.exists(pending):
            return False
        pj = json.load(open(pending, encoding="utf-8"))
        fp, cells, code, data, cmd = pj["fp"], pj.get("cells") or {}, pj.get("code"), pj.get("data"), pj.get("cmd")
        result = result or pj.get("out_dir")
    else:
        ctx = context(argv)
        fp, cells, code, data, cmd = ctx["fp"], {ln: f for ln, f in cell_fps(ctx).items()}, ctx["code"], ctx["data"], shlex.join(ctx["argv"])[:400]
        result = result or ctx["out_dir"]
    append({"kind": "run", "ts": ts, "fp": fp, "code": code, "data": data, "cls": cls, "rc": int(rc),
            "result_path": result, "wall_s": wall_s, "cmd": cmd})
    if int(rc) == 0:
        for ln, f in cells.items():
            append({"kind": "cell", "ts": ts, "cell_fp": f, "cell": ln, "run_fp": fp, "result_path": result})
    if pending:
        os.remove(pending)
    return True


def log_failure(text):
    p = os.path.join(os.environ.get("REG_DIR", snap.REG), "done-errors.log")
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {text}\n")


def main():
    a = sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["check", "done"])
    p.add_argument("cls")
    p.add_argument("rc", nargs="?", default="0")
    p.add_argument("--recompute", action="store_true")
    p.add_argument("--why", default="")
    p.add_argument("--repeat", type=int, default=0)
    p.add_argument("--result")
    p.add_argument("--pending")
    sep = a.index("--") if "--" in a else len(a)
    o = p.parse_args(a[:sep])
    argv = a[sep + 1:]
    if o.cmd == "check":
        rc, info = check(o.cls, argv, o.recompute, o.why, o.repeat)
        if info and info.get("pending"):
            print(f"GUARD-PENDING {info['pending']}")
            print("GUARD-ARGV " + shlex.join(info["argv"]))
        sys.exit(rc)
    try:
        done(o.cls, o.rc, argv or None, o.result, pending=o.pending)
    except Exception as e:
        log_failure(f"done не записан ({o.cls} rc={o.rc} pending={o.pending}): {e!r}")
        print(f"guard: done не записан: {e!r}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
