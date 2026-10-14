#!/usr/bin/env python3
"""Запуск бэктеста (TK-241, В-4..В-6): ядро для формы и CLI (CEO запускает тем же кодом).
  python tools/launch/launch.py plan   --hyps Г-04,Г-05 --months 2026-01,2026-02 [--axis keep_q=5,10] [--json]
  python tools/launch/launch.py submit --hyps … --months … [--axis …] [--dry-run] [--confirm-bin]
  python tools/launch/launch.py status
Дедуп — только по правилу Летописи (master.have_hashes: хеш есть = есть строки results по месяцу); свой хеш не изобретается
(tools/registry/cellhash.py). После постановки ничего не уходит нейросетям (В-6): ни тикетам, ни claude, ни Telegram."""
import argparse, collections, datetime, json, math, os, posixpath, re, shlex, subprocess, sys, time

REQ_RE = re.compile(r"^L-\d{8}-\d{6}\.json$")
HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", ".."))
REG = os.environ.get("LAUNCH_REGISTRY") or os.path.join(ROOT, "docs", "registry")   # на calc — снимок /data/launch/registry (только чтение)
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


ROUTES_FILE = os.path.join(HERE, "routes.json")
COUNTABLE = ("cells", "monthly")   # маршруты, которые кодирует --cells (прочее форма не ставит: посчитано вне Летописи / нет данных / нет кодировки / R1)


def build_routes(protocol="P-14", fraction=1, out=ROUTES_FILE):
    """Маршрут каждой клетки сетки по тем же master.LINES/OUTSIDE/R1COL/R1X/MONTHLY, что и master.plan (строки 592-612); пишет routes.json
    (нужна data/registry.sqlite этой машины — серверу достаточно готового файла). {routes:{хеш: маршрут}, lines:{хеш: «форма набор»},
    monthly:{хеш:{мес: строка}}, sets:{набор: спецификация}}; маршрут: cells | monthly | outside | nodata | noline | r1route."""
    import master, cellhash as ch, cellhash_reg
    c = master.connect()
    grids, _ = master.build_grid(c, protocol, fraction, None, master.rules_of(c, protocol))
    c.close()
    r2b = master.r2b_lines()
    sets = {**cellhash_reg.load_sets(), **master.MSETS}
    routes, lines, monthly, used = {}, {}, {}, set()
    for cells in grids.values():
        for _, cfg in cells:
            h = ch.cell_hash(cfg)
            if h in routes:
                continue
            if h in master.R1COL and master.R1COL[h] in master.R1_NODATA:
                routes[h] = "nodata"
            elif h in master.OUTSIDE or h in master.R1COL or h in master.R1X or master.r2b_covers(h, r2b):
                routes[h] = "outside"
            elif h in master.MONTHLY:
                routes[h] = "monthly"
                monthly[h] = master.MONTHLY[h]
                used |= {ln.split()[1] for ln in master.MONTHLY[h].values()}
            elif h in master.LINES:
                ln = master.LINES[h][0]
                routes[h] = "cells" if ln else "noline"
                if ln:
                    lines[h] = ln
                    used.add(ln.split()[1])
            else:
                routes[h] = "r1route"
    doc = {"protocol": protocol, "fraction": fraction, "generated": now_iso(), "routes": routes, "r2_re": master.R2_EXIT_RE,
           "lines": lines, "monthly": monthly, "sets": {u: sets[u] for u in sorted(used)}}
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        json.dump(doc, f, ensure_ascii=False, sort_keys=True)
    return doc


def _wlines(path, lines):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("".join(x + "\n" for x in lines))


def _form_parts(forms):
    """Хвосты форм --cells (как master.plan.write_set): [(ttl, exit-form|None, early|None)] по форме «вход-стоп-тейк-дедлайн-ttl…»."""
    import re
    return [re.search(r"-ttl(\d+)(?:-(?!early[\d.]+$)(.+?))?(?:-early([\d.]+))?$", fm) for fm in forms]


def line_is_r2(line, r2re):
    """Строка --cells требует r2-бинаря (cargo feature r2; ExitForm::is_r2) — форма запуска ставит один бинарь на запуск."""
    import re
    return any(re.match(r2re, m.group(2)) for m in _form_parts([line.split()[0]]) if m and m.group(2))


def write_set(stem, lines, sets):
    """<stem>.cells / .args — строки --cells «форма набор» и аргументы bounce-grid к ним (формы выхода и наборы); как master.plan.write_set."""
    import re
    lines = sorted(set(lines))
    forms = sorted({ln.split()[0] for ln in lines})
    args = []
    for e in sorted({fm.split("-", 1)[0] for fm in forms}):
        args.append(f"--entry-form {e}")
    tails = _form_parts(forms)
    for t in sorted({m.group(1) for m in tails if m}, key=int):
        args.append(f"--entry-ttl-secs {t}")
    for x in sorted({m.group(2) for m in tails if m and m.group(2)}):
        args.append(f"--exit-form {x}")
    for x in sorted({m.group(3) for m in tails if m and m.group(3)}, key=float):
        args.append(f"--early-exit-secs {x}")
    parts = [re.match(r"^(.+?)-(.+?)-(.+?)-(\d+)-ttl", fm) for fm in forms]
    for flag, g in (("--stop-form", 2), ("--take-form", 3), ("--deadline-secs", 4)):
        for v in sorted({m.group(g) for m in parts if m}):
            args.append(f"{flag} {v}")
    for u in sorted({ln.split()[1] for ln in lines}):
        args.append(f"--set {u}:{sets[u]}")
    if stem:
        _wlines(stem + ".cells", lines)
        _wlines(stem + ".args", args)
    return lines, args


def prepare_plan(p, plan_dir=None, doc=None):
    """План-каталог счёта для раннера (day-cells.sh: $P/$PLAN-<ГГГГ-ММ>.cells/.args; общие L.cells/L.args пусты — клетки заданы по месяцам,
    потому что хеш может быть посчитан не во всех выбранных месяцах). plan_dir=None — только подсчёт. -> ({месяц: число строк --cells}, нужен ли r2-бинарь)"""
    doc = doc or json.load(open(ROUTES_FILE, encoding="utf-8"))
    out, needs_r2 = {}, False
    if plan_dir:
        os.makedirs(plan_dir, exist_ok=True)
        for ext in ("cells", "args"):
            open(os.path.join(plan_dir, f"L.{ext}"), "w").close()
    for m in p["months"]:
        todo = [c["hash"] for c in p["cells"] if m in c["new_months"]]
        lines = [doc["lines"][h] if h in doc["lines"] else doc["monthly"][h][m] for h in todo]
        out[m] = len(set(lines))
        r2 = any(line_is_r2(ln, doc["r2_re"]) for ln in lines)
        needs_r2 = needs_r2 or r2
        if plan_dir:
            write_set(os.path.join(plan_dir, f"L-{m}"), lines, doc["sets"])
            _wlines(os.path.join(plan_dir, f"L-{m}.bin"), ["r2" if r2 else "std"])
    if plan_dir:
        _wlines(os.path.join(plan_dir, "months.txt"), [m for m in p["months"] if out[m]])
    return out, needs_r2


def load_routes(path=None):
    path = path or ROUTES_FILE
    if not os.path.exists(path):
        raise SystemExit(f"нет {path}: python tools/launch/launch.py routes (на машине с data/registry.sqlite)")
    return json.load(open(path, encoding="utf-8"))["routes"]


def queued_months(cfg):
    """{хеш: {месяцы}} из run.json запусков формы (<run_dir>/*/run.json, поле new_hm): поставлено в очередь, но ещё не в Летописи —
    повторный submit того же набора = «нечего считать» (В-7)."""
    out = collections.defaultdict(set)
    try:
        names = os.listdir(cfg["run_dir"])
    except OSError:
        return {}
    for n in names:
        try:
            for h, ms in json.load(open(os.path.join(cfg["run_dir"], n, "run.json"), encoding="utf-8")).get("new_hm", {}).items():
                out[h] |= set(ms)
        except (OSError, ValueError):
            pass
    return out


def overlay_rows(cfg):
    """Результаты, посчитанные формой на этом сервере (<run_dir>/*/overlay.jsonl, postload.py); канон Летописи они не трогают (В-7)."""
    out = []
    try:
        names = sorted(os.listdir(cfg["run_dir"]))
    except OSError:
        return out
    for n in names:
        try:
            out += [json.loads(l) for l in open(os.path.join(cfg["run_dir"], n, "overlay.jsonl"), encoding="utf-8") if l.strip()]
        except OSError:
            pass
    return out


def overlay_have(cfg):
    out = collections.defaultdict(set)
    for r in overlay_rows(cfg):
        out[r["cell_id"]].add(r["month"])
    return out


def postload_errors(cfg):
    """Строки <run_dir>/*/postload.log (ошибка psim/загрузки оверлея) — одна красная строка на вкладке «Запуск»."""
    out = []
    try:
        names = sorted(os.listdir(cfg["run_dir"]))
    except OSError:
        return out
    for n in names:
        try:
            out += [l.strip() for l in open(os.path.join(cfg["run_dir"], n, "postload.log"), encoding="utf-8") if l.strip()]
        except OSError:
            pass
    return out


def validate(hyps, months, axes=None, cfg=None, rows=None):
    """Белые списки (В-7): id гипотез из hypotheses.jsonl, месяцы config.json, значения осей из сетки выбранных гипотез. ValueError — вне списка."""
    cfg = cfg or load_config()
    if not all(isinstance(x, str) for x in [*hyps, *months]) or not isinstance(axes or {}, dict):
        raise ValueError("ожидались списки строк и объект осей")
    bad = [h for h in hyps if h not in {x["id"] for x in hyp_list()}]
    if bad:
        raise ValueError(f"гипотезы вне списка: {bad[:3]}")
    bad = [m for m in months if m not in cfg["months"]]
    if bad:
        raise ValueError(f"месяцы вне списка: {bad[:3]}")
    ga = grid_axes(rows)
    for k, vs in (axes or {}).items():
        ok = {str(v) for h in hyps for v in ga.get(h, {}).get(k, [])}
        if not isinstance(vs, list) or any(str(v) not in ok for v in vs):
            raise ValueError(f"ось {k!r}: значения вне сетки")


def plan(hyps, months, axes=None, rows=None, have=None, cfg=None, routes=None, queued=None):
    """Сухой прогон в единицах хеш×месяц (В-224). axes: {param: [значения]} — фильтр сетки (нет = все значения).
    К счёту — только клетки, которые кодирует --cells (routes: хеш -> маршрут; нет клетки в routes = кодируется); остальное — hm_off
    (off_by по маршрутам: outside — посчитано вне Летописи, nodata — колонки нет в сигналах, noline — нет кодировки, r1route — маршрут R1).
    -> {grid, have, new, off, hm_*, cells:[{hyp,hash,axes,route,have_months,new_months}], axis_cov:{param:{value:[посчитано, всего]}}}"""
    cfg = cfg or load_config()
    if routes is None:
        routes = load_routes() if rows is None else {}
    if rows is None:
        rows = plan_rows(cfg["protocol"], cfg["fraction"])
    if have is None:
        import master
        have = master.have_hashes()
        for h, ms in overlay_have(cfg).items():   # посчитано = канон-снимок ∪ оверлей сервера (В-7)
            have[h] = have.get(h, set()) | ms
    axes = {k: {str(x) for x in v} for k, v in (axes or {}).items() if v}
    months = list(months)
    queued = queued_months(cfg) if queued is None else queued
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
        route = routes.get(r["hash"], "cells")
        qd = [m for m in months if m not in got and m in queued.get(r["hash"], ())]
        todo = [m for m in months if m not in got and m not in qd]
        cells.append({"queued_months": qd, "hyp": r["hyp"], "hash": r["hash"], "axes": r["axes"], "route": route, "have_months": hm,
                      "new_months": todo if route in COUNTABLE else [], "off_months": [] if route in COUNTABLE else todo})
    hm_have = sum(len(c["have_months"]) for c in cells)
    hm_new = sum(len(c["new_months"]) for c in cells)
    off_by = collections.Counter()
    for c in cells:
        off_by[c["route"]] += len(c["off_months"])
    return {"hm_queued": sum(len(c["queued_months"]) for c in cells),
            "grid": len(cells), "have": sum(1 for c in cells if not c["new_months"] and not c["off_months"]),
            "new": sum(1 for c in cells if c["new_months"]), "off": sum(1 for c in cells if c["off_months"]),
            "hm_grid": len(cells) * len(months), "hm_have": hm_have, "hm_new": hm_new, "hm_off": sum(off_by.values()),
            "off_by": {k: v for k, v in off_by.items() if v}, "months": months, "cells": cells,
            "axis_cov": {k: dict(v) for k, v in cov.items()}}


def hyp_list():
    """[{id, family, title}] из docs/registry/hypotheses.jsonl."""
    out = []
    for line in open(os.path.join(REG, "hypotheses.jsonl"), encoding="utf-8"):
        if line.strip():
            d = json.loads(line)
            out.append({"id": d["id"], "family": d.get("family", ""), "title": d.get("title", "")})
    return out


def v227_preset():
    """Гипотезы таблицы «| Г-…» в spec-v227 (в таблице 54 строки, не 56: недостающие не выдумываются)."""
    import re
    ids, rows = [], 0
    for line in open(os.path.join(ROOT, "docs", "research", "spec-v227-2026-10-12.md"), encoding="utf-8"):
        m = re.match(r"^\|\s*(Г-\d+(?:\s*,\s*Г-\d+)*)\s*\|", line)
        if m:
            rows += 1
            ids += [x for x in re.findall(r"Г-\d+", m.group(1)) if x not in ids]
    return ids, rows


def b1_stack():
    """Фиксированный стек B1: строка из tools/compute/hyp_stack.py (не копия) + cfg первой клетки сетки."""
    import re
    src = open(os.path.join(ROOT, "tools", "compute", "hyp_stack.py"), encoding="utf-8").read()
    m = re.search(r"^B1='(.*)'$", src, re.M)
    rows = open(os.path.join(REG, "plan_cells.jsonl"), encoding="utf-8")
    cfg = json.loads(json.loads(next(l for l in rows if l.strip()))["cfg"])
    return {"line": m.group(1) if m else None, "source": "tools/compute/hyp_stack.py", "cfg": cfg}


def grid_axes(rows=None):
    """{гипотеза: {ось: [значения в порядке сетки]}} из клеток сетки."""
    rows = rows if rows is not None else plan_rows()
    out = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in rows:
        for k, v in r["axes"].items():
            if str(v) not in out[r["hyp"]][k]:
                out[r["hyp"]][k].append(str(v))
    return {h: dict(a) for h, a in out.items()}


def meta(cfg=None):
    cfg = cfg or load_config()
    rows = plan_rows(cfg["protocol"], cfg["fraction"])
    ncell = collections.Counter(r["hyp"] for r in rows)
    hy = [dict(h, ncells=ncell.get(h["id"], 0)) for h in hyp_list()]
    v227, v227_rows = v227_preset()
    return {"months": cfg["months"], "partial_month": "2026-10", "hyps": hy, "axes": grid_axes(rows),
            "presets": {"v227": {"ids": v227, "n": len(v227), "note": f"в таблице spec-v227 {v227_rows} строк «Г-», {len(v227)} различных, не 56"},
                        "months": cfg["months"]},
            "stack": b1_stack(), "bin": bin_info(cfg), "queue": cfg["queue"]}


def letopis(hyp=None, month=None, status=None, cfg=None):
    """Строки вкладки «Летопись»: хеш, гипотеза, оси, знак по месяцам (+/−/пусто), вердикт сита."""
    import cellhash_reg
    cfg = cfg or load_config()
    months = cfg["months"]
    pnl = collections.defaultdict(dict)
    for line in open(os.path.join(REG, "results.jsonl"), encoding="utf-8"):
        if line.strip():
            d = json.loads(line)
            pnl[d["cell_id"]][d["month"]] = d.get("pnl_usd")
    byhash = collections.defaultdict(dict)
    for row in cellhash_reg.build():
        for m, v in pnl.get(row[0], {}).items():
            byhash[row[1]][m] = v
    for r in overlay_rows(cfg):
        byhash[r["cell_id"]][r["month"]] = r.get("pnl_usd")
    sieve = {}
    for line in open(os.path.join(REG, "sieve.jsonl"), encoding="utf-8"):
        if line.strip():
            d = json.loads(line)
            sieve.setdefault(d["cell_hash"], []).append(d)
    out = []
    for r in plan_rows(cfg["protocol"], cfg["fraction"]):
        if hyp and r["hyp"] != hyp:
            continue
        got = byhash.get(r["hash"], {})
        sq = ["+" if got.get(m) is not None and got[m] > 0 else "-" if m in got else "" for m in months]
        full = all(sq)
        sv = sieve.get(r["hash"], [])
        verdict = next((k for k in ("eligible", "plateau", "edge") if any(x.get(k) for x in sv)), "")
        if month and not sq[months.index(month)]:
            continue
        st = "full" if full else "part" if any(sq) else "none"
        if status and status != st and status != verdict:
            continue
        out.append({"hash": r["hash"], "hyp": r["hyp"], "axes": r["axes"], "squares": sq, "status": st, "verdict": verdict})
    return {"months": months, "rows": out}


def summary(p):
    off = f"; вне формы {p['hm_off']} ({', '.join(f'{k} {v}' for k, v in sorted(p['off_by'].items()))})" if p["hm_off"] else ""
    que = f"; в очереди {p['hm_queued']}" if p.get("hm_queued") else ""
    return (f"в сетке {p['grid']} хешей × {len(p['months'])} мес. = {p['hm_grid']} хеш-мес.; в Летописи {p['hm_have']}{que}; "
            f"к счёту {p['hm_new']} хеш-мес. ({p['new']} хешей){off}")


def bin_info(cfg):
    """Бинарь счёта (md5/коммит): из config.json; не задан — просит явного подтверждения."""
    return cfg.get("bin") or {"md5": None, "commit": None, "note": "не задан в config.json"}


def on_calc(cfg):
    """Постановка в очередь — только на calc (В-7: форма и CLI живут там, alsched.py рядом); с другой машины работает --dry-run."""
    return os.path.exists(cfg["alsched"])


def now_iso():
    """Метка времени GMT+4 (на calc часовой пояс другой — метка не должна врать)."""
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=4))).strftime("%Y-%m-%dT%H:%M:%S+04:00")


EST_K = 1.5   # правило alsched (manifest.check_estimate): max-runtime не меньше 1,5 x --est, иначе заявка отклонена


def queue_cmd(cfg, run_id, run_dir):
    q, b = cfg["queue"], bin_info(cfg)
    max_runtime = max(q["max_runtime"], math.ceil(EST_K * q["est"]))
    return ["python3", cfg["alsched"], "submit", "--cls", q["cls"], "--name", run_id, "--cores", str(q["cores"]),
            "--mem", str(q["mem"]), "--max-runtime", str(max_runtime), "--est", str(q["est"]), "--", "env", f"BIN={b['path']}",
            f"SCRIPTS={cfg['scripts_dir']}", f"NX={q['cores']}", "bash", os.path.join(HERE, "run-launch.sh"), run_dir]


def precheck(p, dry_run, confirm_bin, cfg, log):
    """Общие отказы постановки (форма и CLI): -> причина или None."""
    if p["hm_new"] == 0:
        log("нечего считать: все выбранные хеш×месяц уже в Летописи или вне формы")
        return "nothing-to-count"
    cap = cfg.get("max_cells_per_run")
    if cap and p["new"] > cap:
        log(f"к счёту {p['new']} хешей > потолка {cap} за запуск (config.json max_cells_per_run) — сузить выбор")
        return "over-cap"
    if not dry_run and not confirm_bin:
        log(f"бинарь {bin_info(cfg)}: подтвердить явно (--confirm-bin), если он отличается от того, на котором посчитана Летопись (guard.equiv)")
        return "bin-not-confirmed"
    if not dry_run and not on_calc(cfg):
        log(f"нет {cfg['alsched']}: постановка — только на calc (запускать launch.py там); здесь работает --dry-run")
        return "not-on-calc"
    return None


def pending_requests(cfg):
    """Запросы формы, ещё не взятые root-юнитом alpha-launch-submit (<request_dir>/L-*.json)."""
    try:
        return sorted(n for n in os.listdir(cfg["request_dir"]) if REQ_RE.match(n))
    except (OSError, KeyError):
        return []


def request_submit(hyps, months, axes, confirm_bin, cfg=None, p=None, log=print):
    """Постановка из формы (юнит alpha-launch без прав на очередь): те же отказы, что у submit, затем запрос атомарно в
    <request_dir>/<run_id>.json; в alsched его ставит root-юнит alpha-launch-submit (process_requests), проверив запрос заново. Один запрос за раз."""
    cfg = cfg or load_config()
    p = p or plan(hyps, months, axes, cfg=cfg)
    why = precheck(p, False, confirm_bin, cfg, log)
    if why:
        return {"ok": False, "why": why, "plan": p}
    if pending_requests(cfg):
        log("предыдущий запрос ещё не взят в очередь — подождите")
        return {"ok": False, "why": "busy", "plan": p}
    run_id = "L-" + time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(cfg["request_dir"], run_id + ".json")
    with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as f:
        json.dump({"hyps": list(hyps), "months": list(months), "axes": axes or {}, "confirm_bin": True}, f, ensure_ascii=False)
    os.replace(path + ".tmp", path)
    log(f"запрос {run_id} записан; в очередь calc его ставит служба alpha-launch-submit")
    return {"ok": True, "why": None, "run_id": run_id, "plan": p}


def process_requests(cfg=None, run_cmd=subprocess.run, log=print):
    """root-юнит alpha-launch-submit (по .path на <request_dir>): каждый запрос проверяется заново белыми списками и потолком
    тем же кодом, ставится submit() под тем же run_id; итог — <run_dir>/<run_id>/result.json (его показывает форма). Запрос удаляется всегда
    (иначе .path зациклится). Имя — строго L-ГГГГММДД-ЧЧММСС.json, не ссылка, ≤ 64 КБ; каталог запуска создаёт root и он должен быть новым."""
    cfg = cfg or load_config()
    n = 0
    for name in sorted(os.listdir(cfg["request_dir"])):
        path = os.path.join(cfg["request_dir"], name)
        try:
            ok = REQ_RE.match(name) and not os.path.islink(path) and os.path.isfile(path) and os.path.getsize(path) <= 65536
            req = json.load(open(path, encoding="utf-8")) if ok else None
        except (OSError, ValueError):
            req = None
        if not isinstance(req, dict):
            log(f"{name}: не запрос — удалён")
            _unlink(path)
            continue
        rid, lines = name[:-5], []
        run_dir = os.path.join(cfg["run_dir"], rid)
        try:
            os.makedirs(run_dir)
        except OSError:
            log(f"{rid}: каталог запуска уже есть — запрос отклонён")
            _unlink(path)
            continue
        try:
            hyps, months, axes = req.get("hyps") or [], req.get("months") or [], req.get("axes") or {}
            validate(hyps, months, axes, cfg)
            res = submit(hyps, months, axes, False, bool(req.get("confirm_bin")), cfg, run_cmd=run_cmd, log=lines.append, run_id=rid)
            res = {"ok": res["ok"], "why": res.get("why")}
        except Exception as e:
            lines.append(f"запрос отклонён: {e}")
            res = {"ok": False, "why": "invalid"}
        res.update(log=lines, ts=now_iso())
        with open(os.path.join(run_dir, "result.json"), "w", encoding="utf-8", newline="\n") as f:
            json.dump(res, f, ensure_ascii=False)
        _unlink(path)
        log(f"{rid}: {'в очереди' if res['ok'] else res['why']}")
        n += 1
    return n


def _unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


def request_errors(cfg):
    """Новейший запуск формы: отказ root-юнита (кроме «нечего считать») — красная строка на вкладке «Запуск»."""
    try:
        names = sorted(n for n in os.listdir(cfg["run_dir"]) if REQ_RE.match(n + ".json"))
    except OSError:
        return []
    for n in reversed(names):
        try:
            r = json.load(open(os.path.join(cfg["run_dir"], n, "result.json"), encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if r.get("ok") or r.get("why") == "nothing-to-count":
            return []
        return [f"{n}: {r.get('why')}: {(r.get('log') or [''])[-1]}"]
    return []


def submit(hyps, months, axes=None, dry_run=False, confirm_bin=False, cfg=None, p=None, run_cmd=subprocess.run, log=print, doc=None, run_id=None):
    """plan -> К счёту=0 отказ -> потолок -> план-каталог (prepare_plan) -> alsched submit на calc; строка запуска пишется в свой
    каталог сервера <run_dir>/run.json, НЕ в канон Летописи (В-7: канон переносит CEO load-results)."""
    cfg = cfg or load_config()
    p = p or plan(hyps, months, axes, cfg=cfg)
    why = precheck(p, dry_run, confirm_bin, cfg, log)
    if why:
        return {"ok": False, "why": why, "plan": p}
    b = bin_info(cfg)
    run_id = run_id or "L-" + time.strftime("%Y%m%d-%H%M%S")
    run_dir = posixpath.join(cfg["run_dir"], run_id)
    cmd = queue_cmd(cfg, run_id, run_dir)
    per_month, needs_r2 = prepare_plan(p, None if dry_run else os.path.join(run_dir, "plan"), doc)
    if needs_r2 and "r2" not in (b.get("features") or ""):
        log(f"в плане есть формы r2 (ExitForm::is_r2), бинарь {b.get('path')} собран без cargo feature r2")
        return {"ok": False, "why": "r2-bin-needed", "plan": p}
    log("команда: " + shlex.join(cmd))
    log("строк --cells по месяцам: " + ", ".join(f"{m} {n}" for m, n in per_month.items()))
    if dry_run:
        return {"ok": True, "dry_run": True, "run_id": run_id, "cmd": cmd, "per_month": per_month, "plan": p}
    r = run_cmd(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"alsched submit rc={r.returncode}: {(r.stderr or r.stdout)[-300:]}")
        return {"ok": False, "why": "alsched-failed", "plan": p}
    row = {"id": run_id, "ts": now_iso(), "kind": "launch", "hyps": sorted(hyps), "months": list(months),
           "axes": axes or {}, "bin": b, "cmd": shlex.join(cmd), "hm_new": p["hm_new"], "new_hm": {c["hash"]: c["new_months"] for c in p["cells"] if c["new_months"]},
           "source": "tools/launch/launch.py"}
    with open(os.path.join(run_dir, "run.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(row, f, ensure_ascii=False, sort_keys=True)
    return {"ok": True, "run_id": run_id, "cmd": cmd, "per_month": per_month, "plan": p}


def status(cfg=None, run_cmd=subprocess.run):
    cfg = cfg or load_config()
    cmd = ["python3", cfg["alsched"], "ps"]
    r = run_cmd(cmd if on_calc(cfg) else ["ssh", cfg["ssh_host"], *cmd], capture_output=True, text=True)
    out = (r.stdout or r.stderr).strip()
    busy = sum(1 for l in out.splitlines() if "running" in l.lower())
    return {"text": out, "busy": busy, "warn": busy >= cfg.get("busy_warn_jobs", 1), "errors": postload_errors(cfg) + request_errors(cfg),
            "pending": len(pending_requests(cfg))}


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
    sp.add_parser("process-requests", help="root-юнит alpha-launch-submit: поставить в очередь запросы формы (проверка заново)")
    sp.add_parser("routes", help="пересчитать routes.json (маршруты клеток; нужна data/registry.sqlite)")
    o = ap.parse_args(argv)
    if o.cmd == "routes":
        d = build_routes()
        print(json.dumps(dict(collections.Counter(d["routes"].values())), ensure_ascii=False), "->", ROUTES_FILE)
        return 0
    if o.cmd == "process-requests":
        process_requests()
        return 0   # всегда 0: сбой запроса записан в result.json, юнит не должен упасть и упереться в start-limit
    if o.cmd == "status":
        s = status()
        print(s["text"])
        return 1 if s["warn"] else 0
    axes = {a.split("=", 1)[0]: a.split("=", 1)[1].split(",") for a in o.axis}
    hyps, months = [x for x in o.hyps.split(",") if x], [x for x in o.months.split(",") if x]
    validate(hyps, months, axes)
    if o.cmd == "plan":
        p = plan(hyps, months, axes)
        print(json.dumps({k: v for k, v in p.items() if k != "cells"}, ensure_ascii=False) if o.json else summary(p))
        return 0
    res = submit(hyps, months, axes, o.dry_run, o.confirm_bin)
    return 0 if res["ok"] else 3


if __name__ == "__main__":
    sys.exit(main())
