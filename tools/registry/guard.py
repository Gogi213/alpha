#!/usr/bin/env python3
"""Защита от повторов (TK-081, В-196): реестр спрашивается до счёта и замера. Работает на сервере счёта рядом с snap.py.
  guard.py check <класс> [--recompute --why ТЕКСТ] [--repeat N] -- <команда…>
      rc 0 — считать (stdout: возможно, команда с урезанным --cells: строка `GUARD-CELLS <файл>`),
      rc 3 — всё уже посчитано / замер уже был: «взято из реестра», ссылка на готовый результат,
      rc 2 — отказ (--recompute без --why).
  guard.py done <класс> <rc> [--result ПУТЬ] -- <команда…>   — после конца: запись в журнал отпечатков
Журнал — $REG_DIR/ledger.jsonl (дописывается; канон реестра docs/registry подтягивает его import-auto).
Отпечаток = команда + env + sha скриптов и входных данных + md5 бинарников + git-коммит: «старое» за новое не выдаётся.
Клетка (--cells <файл>, строка «форма набор») имеет свой отпечаток: общий отпечаток без списка клеток + строка + тело её --set.
TK-118 (В-213): проход объявляет ШАГИ — guard.py step <имя> --out ПУТЬ --in КАТ:КАТ -- команда (готовый шаг пропускается, выход на месте);
  guard.py plan --steps ФАЙЛ — выписка «что есть / что считаем / стоимость» до подачи; guard.py equiv — гейт «байт в байт» нового бинарника
  против старого записан в журнал: md5 нового считается за старый для шага (или «*»), готовое старого засчитывается."""
import argparse, hashlib, json, os, re, shlex, shutil, sys, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import snap

SKIP, REFUSE = 3, 2
VOLATILE_ENV = ("BENCH_FROZEN", "BENCH_IOSTATE", "GUARD_OUT")


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


DATERE = re.compile(r"\d{4}-\d{2}-\d{2}")
ANY = object()


def split_days(argv):
    """argv без `--day D` / `--day=D` -> (остаток, сутки по порядку без повторов). Сутки — измерение отпечатка единицы (клетка × сутки)."""
    out, days, i = [], [], 0
    while i < len(argv):
        a = argv[i]
        if a == "--day" and i + 1 < len(argv):
            v = argv[i + 1]; i += 2
        elif a.startswith("--day="):
            v = a[6:]; i += 1
        else:
            out.append(a); i += 1; continue
        if v not in days:
            days.append(v)
    return out, days


def _day_keep(day):
    """Данные суток: сами сутки и следующие (перенос круга через полночь, --carry-root)."""
    import datetime
    try:
        d = datetime.date.fromisoformat(day)
    except ValueError:
        return frozenset({day})
    return frozenset({day, (d + datetime.timedelta(days=1)).isoformat()})


def _dated_ok(text, keep):
    """Запись с датой чужих суток к данным этих суток не относится; без даты — относится всегда."""
    return keep is ANY or all(x in keep for x in DATERE.findall(text))


def csv_fp(path, keep):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for ln in f:
            if _dated_ok(ln.decode("utf-8", "replace"), keep):
                h.update(ln)
    return h.hexdigest()[:16]


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


def dir_fp(path, keep=ANY, rel=False):
    """Отпечаток каталога данных: имена, размеры и mtime на двух уровнях (не больше DIRCAP записей, срез помечается)."""
    h, n = hashlib.sha256(), 0
    base = path.rstrip("/")
    stack = [(path, 0)]
    while stack and n < DIRCAP:
        d, lvl = stack.pop()
        try:
            ents = sorted(os.scandir(d), key=lambda e: e.name)
        except OSError:
            continue
        for e in ents:
            if DERIVED.search(e.name) or not _dated_ok(e.name, keep):
                continue
            n += 1
            if n >= DIRCAP:
                break
            try:
                st = e.stat()   # симлинк — по цели (ферма ссылок на эпохи: смена файла-цели должна быть видна)
            except OSError:
                try:
                    st = e.stat(follow_symlinks=False)
                except OSError:
                    continue
            isdir = e.is_dir()
            nm = (d[len(base):] if rel else d) + "/" + e.name   # rel: имена от корня входа (копия/ферма ссылок в другом каталоге — та же версия)
            if isdir and os.name == "nt":   # NTFS обновляет mtime каталога в родителе лениво — отпечаток мигал бы
                h.update(f"{nm}/;".encode())
            else:
                h.update(f"{nm}|{st.st_size}|{st.st_mtime_ns};".encode())
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


def explicit_inputs(argv):
    """Входы, названные явно: входные флаги (и строк --extra-runs) + env GUARD_INPUTS=каталог:каталог (декларация задания-скрипта)."""
    runs = [argv] + _extra_runs_argvs(argv)
    c = {v.rstrip("/") for r in runs for f in INPUT_FLAGS for v in _flag_values(r, f)}
    return c | {v.rstrip("/") for v in os.environ.get("GUARD_INPUTS", "").split(os.pathsep) if v}


def input_dirs(texts, argv, out_dir):
    """Каталоги данных — только ВХОДЫ: значения входных флагов (и строк --extra-runs) и упомянутые пути внутри явных корней
    (GUARD_DATA_ROOTS). Выход (--out-dir и подкаталоги) и всё, что пишет задание, исключено — иначе прогон сам меняет свой отпечаток."""
    roots = [r for r in os.environ.get("GUARD_DATA_ROOTS", ":".join(DATA_ROOTS)).split(":") if r]
    runs = [argv] + _extra_runs_argvs(argv)
    cand = explicit_inputs(argv)
    for t in texts:
        for m in snap.PATHRE.findall(t):
            m = m.rstrip("/")
            if any(_under(m, r) or _under(r, m) for r in roots):
                cand.add(m)
    outs = {o.rstrip("/") for r in runs for o in _flag_values(r, "--out-dir")} | ({out_dir.rstrip("/")} if out_dir else set())         | ({os.environ["GUARD_OUT"].rstrip("/")} if os.environ.get("GUARD_OUT") else set())
    return sorted(d for d in cand if os.path.isdir(d) and not any(_under(d, o) or _under(o, d) for o in outs))


def _binaries(argv, env):
    bins = {}
    cands = [shutil.which(argv[0]) if argv else None, shutil.which("lob")] + [v for v in env.values() if os.path.isabs(v)]
    cands += [a for a in argv if os.path.isabs(a)]
    for c in cands:
        if c and os.path.isfile(c) and os.access(c, os.X_OK) and os.path.getsize(c) > snap.MAXCOPY:
            bins[c] = snap.sha(c, 5)
    return bins


def equiv_map(led, scope=None):
    """{md5 нового: md5 старого} по записям equiv (гейт «байт в байт», TK-118) для шага scope и для «*»; цепочки сворачиваются к первому старому."""
    m = {r["new"]: (r["old"], r.get("old_path")) for r in led
         if r.get("kind") == "equiv" and r.get("scope") in ("*", scope) and r.get("new") and r.get("old")}
    for k in list(m):
        seen = {k}
        while m[k][0] in m and m[k][0] not in seen:
            seen.add(m[k][0])
            m[k] = m[m[k][0]]
    return m


def norm_map():
    """TK-118: GUARD_NORM=путь=метка;путь=метка — логические имена путей шага (рабочее дерево суток у каждого прогона своё); выход шага (GUARD_OUT) — «<out>»."""
    m = [tuple(x.split("=", 1)) for x in os.environ.get("GUARD_NORM", "").split(";") if "=" in x]
    if os.environ.get("GUARD_OUT"):
        m.append((os.environ["GUARD_OUT"], "<out>"))
    return sorted(((a.rstrip("/"), b) for a, b in m), key=lambda t: -len(t[0]))


def nrm(x, m):
    if not m or not isinstance(x, str):
        return x
    for a, b in m:
        if x == a or x.startswith(a + "/") or x.startswith(a + "="):
            return b + x[len(a):]
        if a in x:
            x = x.replace(a, b)
    return x


def context(argv, scope=None):
    """Общая часть отпечатка: всё, что влияет на результат, кроме списка клеток и выхода."""
    argv = core_argv(argv)
    env = {k: v for k, v in sorted(os.environ.items()) if snap.ENVRE.match(k) and k not in VOLATILE_ENV}
    files, inputs, bins = snap.collect(argv)
    go = os.environ.get("GUARD_OUT", "").rstrip("/")
    if go:   # выход шага возникает в ходе счёта: не вход и не скрипт
        files, inputs = ({p: v for p, v in x.items() if not _under(p, go)} for x in (files, inputs))
    bins = {**{p: b["md5"] for p, b in bins.items()}, **_binaries(argv, env)}
    eq = equiv_map(read_ledger(), scope)
    # TK-118: доказанно равный бинарник — тот же код: md5 и путь в команде берутся от старого (его отпечаток уже в журнале)
    swap = {p: eq[v][1] for p, v in bins.items() if v in eq and eq[v][1]}
    bins = {swap.get(p, p): eq[v][0] if v in eq else v for p, v in bins.items()}
    argv = [swap.get(x, x) for x in argv]
    texts = [" ".join(argv)] + [open(p, encoding="utf-8", errors="replace").read() for p in files if os.path.getsize(p) < 200_000]
    rest, cells, sets = split_cells_args(argv)
    rest, days = split_days(rest)
    out_dir = next((rest[i + 1] for i, x in enumerate(rest[:-1]) if x == "--out-dir"), None)
    rest = [x for i, x in enumerate(rest) if x != "--out-dir" and not (i and rest[i - 1] == "--out-dir")]
    dir_list = input_dirs(texts, rest, out_dir)
    keep0 = frozenset() if days else ANY   # в режиме «сутки» общая часть — только записи без даты, даты — в отпечатке единицы
    nm = norm_map()
    dirs = {nrm(d, nm): dir_fp(d, keep0, rel=bool(nm)) for d in dir_list}
    code = {"bins": dict(sorted(bins.items()))}
    csvs = [p for p in list(files) + list(inputs) if days and p.endswith(".csv") and p != cells and os.path.getsize(p) <= snap.MAXHASH]
    data = {nrm(p, nm): (v.get("sha256") or [v.get("size"), v.get("mtime")]) for p, v in sorted(inputs.items()) if p not in csvs}
    data["csv"] = {nrm(p, nm): csv_fp(p, keep0) for p in sorted(csvs)}
    data["dirs"] = dirs
    scripts = {nrm(p, nm): v["sha256"] for p, v in sorted(files.items()) if p != cells and p not in csvs}
    return {"fp": _h({"argv": [nrm(x, nm) for x in rest], "env": env, "code": code, "data": data, "scripts": scripts}),
            "code": _h(code), "data": _h(data), "cells": cells, "sets": sets, "out_dir": out_dir, "argv": argv, "days": days,
            "day_inputs": (dir_list, sorted(csvs)),
            "nodirs": not any(os.path.isdir(d) for d in explicit_inputs(rest))}


def unit_data_fp(ctx, day):
    """Версия данных ОДНИХ суток: записи входов с датой этих (и следующих) суток."""
    keep = _day_keep(day)
    dir_list, csvs = ctx["day_inputs"]
    return _h({"dirs": {d: dir_fp(d, keep) for d in dir_list}, "csv": {p: csv_fp(p, keep) for p in csvs}})


def cell_fps(ctx):
    """{ключ: отпечаток}. Ключ — строка клетки; с --day — «клетка @сутки» (единица = клетка × сутки × код × данные)."""
    lines = []
    if ctx["cells"] and os.path.isfile(ctx["cells"]):
        lines = [ln.strip() for ln in open(ctx["cells"], encoding="utf-8") if len(ln.split()) == 2]
    if not ctx["days"]:
        return {ln: _h({"ctx": ctx["fp"], "line": ln, "set": ctx["sets"].get(ln.split()[1], "")}) for ln in lines}
    out, ctx["pairs"] = {}, {}
    for day in ctx["days"]:
        ud = unit_data_fp(ctx, day)
        for ln in lines or [""]:
            k = f"{ln} @{day}"
            out[k] = _h({"ctx": ctx["fp"], "line": ln, "set": ctx["sets"].get(ln.split()[1], "") if ln else "", "day": day, "data": ud})
            ctx["pairs"][k] = (ln, day)
    return out


def _swap(argv, flag, old, new):
    return [new if (i and argv[i - 1] == flag and x == old) else x for i, x in enumerate(argv)]


def _pending(ctx, cls, calc_cells, out_dir, argv):
    d = os.path.join(os.environ.get("REG_DIR", snap.REG), "pending")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, f"{ctx['fp']}-{int(time.time() * 1000)}.json")
    json.dump({"fp": ctx["fp"], "cls": cls, "cells": calc_cells, "out_dir": out_dir, "code": ctx["code"], "data": ctx["data"],
               "cmd": shlex.join(argv)[:400],
               "declared": os.environ.get("GUARD_CELLS_DECLARED")}, open(path, "w", encoding="utf-8"), ensure_ascii=False)
    return path


def check(cls, argv, recompute=False, why="", repeat=0, say=print, scope=None):
    """-> (rc, info). info: fp, pending (путь записи ожидания для done), argv (команда для запуска: клетки/выход урезаны, если считается часть)."""
    if recompute and not why.strip():
        say("guard: --recompute требует --why «причина» (напр. «изменился код») — отказ")
        return REFUSE, None
    ctx = context(argv, scope)
    led = read_ledger()
    done_runs = [r for r in led if r.get("kind") == "run" and r.get("fp") == ctx["fp"] and r.get("rc") == 0]
    run_argv = list(argv)
    calc, out_dir = {}, ctx["out_dir"]
    if recompute:
        append({"kind": "recompute", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "fp": ctx["fp"], "cls": cls, "why": why,
                "cmd": shlex.join(ctx["argv"])[:400]})
    elif cls not in ("wave", "stand", "measure") and ctx["nodirs"]:
        say(f"guard: ВНИМАНИЕ — версия данных неизвестна (в отпечатке {ctx['fp']} нет ни одного явного входа: входного флага или GUARD_INPUTS): из реестра не берём, "
            f"считаем заново. Объявить входы: env GUARD_INPUTS=каталог:каталог (входит в отпечаток)")
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
            missing = {k: f for k, f in cfp.items() if f not in got}
            unit = "пар «клетка × сутки»" if ctx["days"] else "клеток"
            if not missing:
                say(f"guard: взято из реестра — все {len(cfp)} {unit} уже посчитаны (отпечаток {ctx['fp']}); считать нечего")
                for f in list(got)[:3]:
                    say(f"  результат: {got[f].get('result_path')}")
                return SKIP, list(got.values())[-1]
            calc = missing
            if got:
                part = (ctx["cells"] or out_dir or "guard") + f".todo-{ctx['fp']}"
                if ctx["days"]:   # выход один на запуск: считаем произведение недостающих клеток на недостающие сутки (готовые внутри — пересчёт ради простоты)
                    mlines = list(dict.fromkeys(ctx["pairs"][k][0] for k in missing))
                    mdays = list(dict.fromkeys(ctx["pairs"][k][1] for k in missing))
                    calc = {k: f for k, f in cfp.items() if ctx["pairs"][k][0] in mlines and ctx["pairs"][k][1] in mdays}
                    run_argv = [x for i, x in enumerate(run_argv) if not (x == "--day" or x.startswith("--day=") or (i and run_argv[i - 1] == "--day"))]
                    for d in mdays:
                        run_argv += ["--day", d]
                else:
                    mlines = list(missing)
                if ctx["cells"]:
                    with open(part, "w", encoding="utf-8", newline="\n") as pf:
                        pf.write("\n".join(mlines) + "\n")
                    run_argv = _swap(run_argv, "--cells", ctx["cells"], part)
                if out_dir:   # урезанный счёт — в свой каталог: иначе перезапишет rounds.csv прежних клеток
                    out_dir = out_dir.rstrip("/") + f"-part-{ctx['fp'][:8]}"
                    run_argv = _swap(run_argv, "--out-dir", ctx["out_dir"], out_dir)
                else:
                    say("guard: ВНИМАНИЕ — --out-dir не найден в команде, выход урезанного счёта не разведён")
                say(f"guard: взято из реестра {len(got)} {unit} из {len(cfp)}; считается {len(calc)} → {out_dir}")
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
        declared = pj.get("declared")
    else:
        ctx = context(argv)
        fp, cells, code, data, cmd = ctx["fp"], {ln: f for ln, f in cell_fps(ctx).items()}, ctx["code"], ctx["data"], shlex.join(ctx["argv"])[:400]
        result = result or ctx["out_dir"]
        declared = os.environ.get("GUARD_CELLS_DECLARED")
    declared = declared if declared and os.path.isfile(declared) else (os.path.join(result, "cells-declared.txt") if result else None)
    if not cells and declared and os.path.isfile(declared):
        # TK-089 п.2: оркестратор (R1/R2/П-12/TK-084) строит клетки на лету и объявляет их в файле GUARD_CELLS_DECLARED (env при подаче) или <выход>/cells-declared.txt («форма набор» по строке)
        lines = [ln.strip() for ln in open(declared, encoding="utf-8") if len(ln.split()) == 2]
        cells = {ln: _h({"ctx": fp, "line": ln}) for ln in lines}
    append({"kind": "run", "ts": ts, "fp": fp, "code": code, "data": data, "cls": cls, "rc": int(rc),
            "result_path": result, "wall_s": wall_s, "cmd": cmd})
    if int(rc) == 0:
        for ln, f in cells.items():
            append({"kind": "cell", "ts": ts, "cell_fp": f, "cell": ln, "run_fp": fp, "result_path": result})
    if pending:
        os.remove(pending)
    return True


_STEP_BASE = {}


def _step_env(ins, out=None, norm=()):
    if norm:
        os.environ["GUARD_NORM"] = ";".join(norm)
    else:
        os.environ.pop("GUARD_NORM", None)
    if out:
        os.environ["GUARD_OUT"] = out   # выход шага — не вход: исключается из отпечатка каталогов (как --out-dir)
    else:
        os.environ.pop("GUARD_OUT", None)
    base = _STEP_BASE.setdefault("inputs", os.environ.get("GUARD_INPUTS", ""))   # входы шага не копятся от вызова к вызову
    os.environ["GUARD_INPUTS"] = os.pathsep.join([x for x in [base] + list(ins) if x])


def step_state(name, argv, ins=(), out=None, norm=()):
    """-> (готов?, строка реестра | None, число клеток к счёту | None). Готов = отпечаток шага есть в журнале, rc 0, выход на месте."""
    _step_env(ins, out, norm)
    rc, info = check("prod", argv, say=lambda *_: None, scope=name)
    if rc == SKIP:
        r = info[-1] if isinstance(info, list) else info
        return (not out or os.path.exists(out)), r, 0
    if info and info.get("pending"):
        pj = json.load(open(info["pending"], encoding="utf-8"))
        os.remove(info["pending"])
        return False, None, len(pj.get("cells") or {}) or None
    return False, None, None


def step(name, argv, ins=(), out=None, dry=False, recompute=False, why="", norm=()):
    """Шаг прохода: готов — пропуск (rc 0), иначе считает команду и пишет в журнал. dry — только сказать, что было бы."""
    ts0 = time.time()
    _step_env(ins, out, norm)
    say = lambda m: print(f"[step {name}] {m}", flush=True)
    rc, info = check("prod", argv, recompute, why, say=say, scope=name)
    if rc == REFUSE:
        return REFUSE
    if rc == SKIP and out and not os.path.exists(out):
        say(f"отпечаток есть, но выхода {out} нет — считаем заново")
        rc, info = check("prod", argv, True, "выход готового шага отсутствует", say=lambda m: None, scope=name)
    if rc == SKIP:
        say("ГОТОВО — берём из реестра, не считаем")
        return 0
    if dry:
        os.remove(info["pending"])
        say("СЧИТАЕМ (dry)")
        return 0
    import subprocess
    r = subprocess.call(info["argv"])
    done("prod", r, pending=info["pending"], result=out, wall_s=round(time.time() - ts0, 1))
    return r


def parse_steps(path):
    """Файл шагов: строка `имя<TAB>выход<TAB>входы через :<TAB>команда`, # — комментарий."""
    out = []
    for ln in open(path, encoding="utf-8"):
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        f = ln.rstrip("\n").split("\t")
        if len(f) < 4:
            continue
        out.append((f[0], f[1] or None, [x for x in f[2].split(os.pathsep) if x], shlex.split("\t".join(f[3:]))))
    return out


def plan(steps_file, say=print, mark=True):
    """Выписка из Летописи до прохода (В-213): по шагам — готово / считаем; стоимость — по wall_s прошлых прогонов шага."""
    rows = parse_steps(steps_file)
    led = read_ledger()
    have = new = 0
    cost = 0.0
    say(f"ВЫПИСКА {steps_file}: шагов {len(rows)}")
    for name, out, ins, argv in rows:
        ok, r, ncells = step_state(name, argv, ins, out)
        runs = [x.get("wall_s") for x in led if x.get("kind") == "run" and x.get("rc") == 0 and x.get("wall_s")
                and shlex.join(argv)[:60] in (x.get("cmd") or "")]
        est = (sum(runs) / len(runs)) if runs else None
        if ok:
            have += 1
            say(f"  ГОТОВО  {name}: {(r or {}).get('result_path') or out}")
        else:
            new += 1
            cost += est or 0
            say(f"  СЧИТАЕМ {name}" + (f" ({ncells} клеток к счёту)" if ncells else "") + (f", ~{est:.0f} с по прошлым прогонам" if est else ", стоимость неизвестна"))
    say(f"ИТОГО: готово {have}, считаем {new}, оценка ~{cost:.0f} с")
    if mark:
        d = os.path.join(os.environ.get("REG_DIR", snap.REG), "vypiska")
        os.makedirs(d, exist_ok=True)
        key = hashlib.sha256(open(steps_file, "rb").read()).hexdigest()[:16]
        json.dump({"steps_file": steps_file, "key": key, "have": have, "new": new, "est_s": cost,
                   "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z")}, open(os.path.join(d, key + ".json"), "w"), ensure_ascii=False)
    return have, new, cost


def vypiska_marked(steps_file=None):
    """Есть ли отметка выписки (для alsched submit prod > 15 мин): по файлу шагов или любая за последние 12 ч."""
    d = os.path.join(os.environ.get("REG_DIR", snap.REG), "vypiska")
    if not os.path.isdir(d):
        return False
    if steps_file and os.path.isfile(steps_file):
        key = hashlib.sha256(open(steps_file, "rb").read()).hexdigest()[:16]
        return os.path.exists(os.path.join(d, key + ".json"))
    return any(time.time() - e.stat().st_mtime < 12 * 3600 for e in os.scandir(d))


def equiv(new, old, scope, gate):  # new/old — путь (лучше: путь старого нужен, чтобы команда нового совпала по тексту) или md5
    """Гейт «байт в байт» нового бинарника против старого (md5 или путь) -> запись в журнал (TK-118)."""
    def md(x):
        return snap.sha(x, 5) if os.path.isfile(x) else x
    if not gate or not os.path.exists(gate):
        raise SystemExit("guard equiv: нужен --gate <файл с итогом гейта «байт в байт»> (должен существовать)")
    row = {"kind": "equiv", "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "new": md(new), "old": md(old),
           "old_path": old if os.path.isfile(old) else None, "scope": scope, "gate": gate}
    append(row)
    return row


def log_failure(text):
    p = os.path.join(os.environ.get("REG_DIR", snap.REG), "done-errors.log")
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {text}\n")


def main():
    a = sys.argv[1:]
    p = argparse.ArgumentParser()
    p.add_argument("cmd", choices=["check", "done", "step", "plan", "equiv"])
    p.add_argument("cls", nargs="?", default="prod", help="класс; для step — имя шага; для plan/equiv не нужен")
    p.add_argument("--out")
    p.add_argument("--in", dest="ins", default="")
    p.add_argument("--dry", action="store_true")
    p.add_argument("--norm", action="append", default=[], help="step: путь=метка — логическое имя пути рабочего дерева (не входит в ключ)")
    p.add_argument("--steps")
    p.add_argument("--new")
    p.add_argument("--old")
    p.add_argument("--scope", default="*")
    p.add_argument("--gate")
    p.add_argument("rc", nargs="?", default="0")
    p.add_argument("--recompute", action="store_true")
    p.add_argument("--why", default="")
    p.add_argument("--repeat", type=int, default=0)
    p.add_argument("--result")
    p.add_argument("--pending")
    sep = a.index("--") if "--" in a else len(a)
    o = p.parse_args(a[:sep])
    argv = a[sep + 1:]
    if o.cmd == "step":
        sys.exit(step(o.cls, argv, [x for x in o.ins.split(os.pathsep) if x], o.out, o.dry or bool(os.environ.get("GUARD_DRY")), o.recompute, o.why, o.norm))
    if o.cmd == "plan":
        plan(o.steps or o.cls)
        return
    if o.cmd == "equiv":
        print(json.dumps(equiv(o.new, o.old, o.scope, o.gate), ensure_ascii=False))
        return
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
