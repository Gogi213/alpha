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
    """Клетки сетки (docs/registry/plan_cells.jsonl): hyp, hash, axes{}. fraction — число или список долей (TK-272: [1, 2] — F1 и F2);
    одна пара (гипотеза, хеш) в нескольких долях — одна строка (первая доля)."""
    fr = {fraction} if isinstance(fraction, int) else set(fraction)
    out, seen = [], set()
    for line in open(os.path.join(REG, "plan_cells.jsonl"), encoding="utf-8"):
        if line.strip():
            d = json.loads(line)
            if d["protocol"] == protocol and d["fraction"] in fr and (d["hyp"], d["cell_hash"]) not in seen:
                seen.add((d["hyp"], d["cell_hash"]))
                out.append({"hyp": d["hyp"], "hash": d["cell_hash"], "axes": json.loads(d["axes"]), "fraction": d["fraction"]})
    return out


def protocols_of(cfg):
    """[(протокол, фракция)] формы: config.protocols (пары) или один (protocol, fraction) — прежнее поведение (TK-279: П-17 рядом с П-14)."""
    return [tuple(x) for x in cfg.get("protocols") or [(cfg["protocol"], cfg["fraction"])]]


def plan_rows_cfg(cfg):
    """Клетки сеток всех протоколов формы подряд (plan_rows по каждой паре)."""
    return [r for proto, fr in protocols_of(cfg) for r in plan_rows(proto, fr)]


ROUTES_FILE = os.path.join(HERE, "routes.json")
R1_ROUTES = ("r1", "r1x")   # TK-251: R1 не идёт строкой --cells — отбор r1cell.py + psim (r1: отбор по .r1spec; r1x: клетки готовы, только psim)
COUNTABLE = ("cells", "monthly") + R1_ROUTES   # маршруты, которые форма умеет посчитать (прочее: посчитано вне Летописи / нет данных / нет кодировки / композит R1)
OFF_LABELS = {"outside": "посчитано вне Летописи", "nodata": "нет данных", "noline": "нет кодировки", "r1route": "маршрут R1",
              "r1comp": "композит R1 (Г-64/73/83) — отбора в форме нет"}   # причина «вне формы» словами для страницы (TK-247: «посчитано» — только где считали)
OFF_ROUTES = list(OFF_LABELS)   # код в coverage(): 0 — форма считает, i+1 — OFF_ROUTES[i]


def build_routes(protocol="P-14", fraction=1, out=ROUTES_FILE, extra=()):
    """Маршрут каждой клетки сетки по тем же master.LINES/OUTSIDE/R1COL/R1X/MONTHLY, что и master.plan (строки 592-612); пишет routes.json
    (нужна data/registry.sqlite этой машины — серверу достаточно готового файла). {routes:{хеш: маршрут}, lines:{хеш: «форма набор»},
    monthly:{хеш:{мес: строка}}, sets:{набор: спецификация}, r1spec:{хеш: [гипотеза, колонка, знак, keep_q, ось ctx, ctx_q]}};
    маршрут: cells | monthly | r1 | r1x | outside | r1comp | nodata | noline | r1route (TK-251: r1col → r1 для колонок со знаком, r1comp — композиты common_q)."""
    import master, cellhash as ch, cellhash_reg
    sys.path.insert(0, os.path.join(ROOT, "tools", "compute"))
    import r1cell
    c = master.connect()
    items = {}
    for fr in ([fraction] if isinstance(fraction, int) else fraction):   # TK-272: доли F1+F2 одним файлом
        for hyp, cells in master.build_grid(c, protocol, fr, None, master.rules_of(c, protocol))[0].items():
            items.setdefault(hyp, []).extend(cells)
    items = list(items.items())
    for pr, fr in extra:   # TK-279: дополнительные протоколы (П-17) — их сетки после основной; гипотеза может быть в обеих (хеши разные)
        items += list(master.build_grid(c, pr, fr, None, master.rules_of(c, pr))[0].items())
    c.close()
    r2b = master.r2b_lines()
    sets = {**cellhash_reg.load_sets(), **master.MSETS}
    routes, lines, monthly, used, r1spec = {}, {}, {}, set(), {}
    for hyp, cells in items:
        for av, cfg in cells:
            h = ch.cell_hash(cfg)
            if h in routes:
                continue
            if h in master.R1COL and master.R1COL[h] in master.R1_NODATA:
                routes[h] = "nodata"
            elif h in master.R1COL:
                sign = r1cell.sign_of(master, hyp, av["col"]) if "col" in av else None
                routes[h] = "r1" if sign else "r1comp"
                if sign:
                    r1spec[h] = [hyp, av["col"], sign, str(av["keep_q"]), r1cell.CTX_AXIS.get(hyp, "") if "ctx_q" in av else "", str(av.get("ctx_q", ""))]
            elif h in master.R1X:
                routes[h] = "r1x"
            elif h in master.OUTSIDE or master.r2b_covers(h, r2b):
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
           "lines": lines, "monthly": monthly, "sets": {u: sets[u] for u in sorted(used)}, "r1spec": r1spec}
    if extra:
        doc["protocols"] = [[protocol, fraction]] + [list(x) for x in extra]
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
    потому что хеш может быть посчитан не во всех выбранных месяцах). plan_dir=None — только подсчёт. -> ({месяц: число строк --cells}, нужен ли r2-бинарь)
    TK-251: R1 (маршруты r1/r1x) в --cells не идёт: <план>/L-<мес>.r1spec (hash,hyp,col,sign,keep_q,ctx_axis,ctx_q — для r1cell.py apply), L-<мес>.r1x (хеши с готовыми
    клетками — только psim), r1months.txt (месяцы с R1); без R1 этих файлов нет — план прежний байт в байт."""
    doc = doc or json.load(open(ROUTES_FILE, encoding="utf-8"))
    out, needs_r2, r1months = {}, False, []
    if plan_dir:
        os.makedirs(plan_dir, exist_ok=True)
        for ext in ("cells", "args"):
            open(os.path.join(plan_dir, f"L.{ext}"), "w").close()
    for m in p["months"]:
        todo = [c["hash"] for c in p["cells"] if m in c["new_months"]]
        rt = doc.get("routes", {})
        r1 = [h for h in todo if rt.get(h) == "r1"]
        r1x = [h for h in todo if rt.get(h) == "r1x"]
        todo = [h for h in todo if rt.get(h) not in R1_ROUTES]
        if plan_dir and (r1 or r1x):
            r1months.append(m)
            _wlines(os.path.join(plan_dir, f"L-{m}.r1spec"), ["hash,hyp,col,sign,keep_q,ctx_axis,ctx_q"] + [",".join([h] + doc["r1spec"][h]) for h in sorted(r1)])
            _wlines(os.path.join(plan_dir, f"L-{m}.r1x"), sorted(r1x))
        lines = [doc["lines"][h] if h in doc["lines"] else doc["monthly"][h][m] for h in todo]
        out[m] = len(set(lines))
        r2 = any(line_is_r2(ln, doc["r2_re"]) for ln in lines)
        needs_r2 = needs_r2 or r2
        if plan_dir:
            write_set(os.path.join(plan_dir, f"L-{m}"), lines, doc["sets"])
            _wlines(os.path.join(plan_dir, f"L-{m}.bin"), ["r2" if r2 else "std"])
    if plan_dir:
        _wlines(os.path.join(plan_dir, "months.txt"), [m for m in p["months"] if out[m]])
        if r1months:
            _wlines(os.path.join(plan_dir, "r1months.txt"), r1months)
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


def validate_groups(groups, cfg=None, rows=None):
    """validate() для каждой группы выбора страницы; не больше 200 групп."""
    if not isinstance(groups, list) or len(groups) > 200 or not all(isinstance(g, dict) for g in groups):
        raise ValueError("groups: ожидался список объектов (≤ 200)")
    for g in groups:
        validate(g.get("hyps") or [], g.get("months") or [], g.get("axes") or {}, cfg, rows)


def plan(hyps, months, axes=None, rows=None, have=None, cfg=None, routes=None, queued=None):
    """Сухой прогон в единицах хеш×месяц (В-224). axes: {param: [значения]} — фильтр сетки (нет = все значения).
    К счёту — только клетки, которые кодирует --cells (routes: хеш -> маршрут; нет клетки в routes = кодируется); остальное — hm_off
    (off_by по маршрутам: outside — посчитано вне Летописи, r1col — R1 не считали (данных нет), r1x — клетки R1 есть, $ нет, nodata — колонки нет в сигналах, noline — нет кодировки, r1route — маршрут R1).
    -> {grid, have, new, off, hm_*, cells:[{hyp,hash,axes,route,have_months,new_months}], axis_cov:{param:{value:[посчитано, всего]}}}"""
    cfg = cfg or load_config()
    if routes is None:
        routes = load_routes() if rows is None else {}
    if rows is None:
        rows = plan_rows_cfg(cfg)
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
    out = _totals(cells, months)
    out["axis_cov"] = {k: dict(v) for k, v in cov.items()}
    return out


def _totals(cells, months):
    hm_have = sum(len(c["have_months"]) for c in cells)
    hm_new = sum(len(c["new_months"]) for c in cells)
    off_by = collections.Counter()
    for c in cells:
        off_by[c["route"]] += len(c["off_months"])
    return {"hm_queued": sum(len(c["queued_months"]) for c in cells),
            "grid": len(cells), "have": sum(1 for c in cells if not c["new_months"] and not c["off_months"]),
            "new": sum(1 for c in cells if c["new_months"]), "off": sum(1 for c in cells if c["off_months"]),
            "hm_grid": len(cells) * len(months), "hm_have": hm_have, "hm_new": hm_new, "hm_off": sum(off_by.values()),
            "off_by": {k: v for k, v in off_by.items() if v}, "months": months, "cells": cells}


def _groups_of(hyps, months, axes, groups):
    """Выбор формы: список групп {hyps, months, axes}; нет groups — одна группа из прямоугольника hyps×months×axes (CLI и старый вызов)."""
    return groups if groups else [{"hyps": hyps, "months": months, "axes": axes or {}}]


def plan_groups(groups, rows=None, have=None, cfg=None, routes=None, queued=None):
    """Выбор страницы «гипотеза × месяцы × значения осей» (у каждой гипотезы свой набор месяцев): plan() по каждой группе, затем слияние по хешу
    (объединение месяцев). Итог той же формы, что у plan() (axis_cov пуст) — submit/prepare_plan работают без правок."""
    cfg = cfg or load_config()
    if routes is None:
        routes = load_routes() if rows is None else {}
    if rows is None:
        rows = plan_rows_cfg(cfg)
    if have is None:
        import master
        have = master.have_hashes()
        for h, ms in overlay_have(cfg).items():
            have[h] = have.get(h, set()) | ms
    queued = queued_months(cfg) if queued is None else queued
    merged = {}
    for g in groups:
        for c in plan(g["hyps"], g["months"], g.get("axes"), rows, have, cfg, routes, queued)["cells"]:
            m = merged.get(c["hash"])
            if m is None:
                merged[c["hash"]] = {**c, **{k: list(c[k]) for k in _MKEYS}}
            else:
                for k in _MKEYS:
                    m[k] = list(dict.fromkeys(m[k] + c[k]))
    order = {m: i for i, m in enumerate(cfg["months"])}
    for c in merged.values():
        for k in _MKEYS:
            c[k].sort(key=order.__getitem__)
    months = sorted({m for g in groups for m in g["months"]}, key=order.__getitem__)
    out = _totals(list(merged.values()), months)
    out["hm_grid"] = sum(len(c[k]) for c in merged.values() for k in _MKEYS)   # у каждой гипотезы свои месяцы: не клетки × объединение
    return {**out, "axis_cov": {}}


_MKEYS = ("queued_months", "have_months", "new_months", "off_months")


def _off_code(route):
    return 0 if route in COUNTABLE else OFF_ROUTES.index(route) + 1 if route in OFF_ROUTES else 1


def coverage(cfg=None, rows=None, have=None, routes=None, queued=None):
    """Покрытие страницы по хешам Летописи: {гипотеза: {axes:{ось:[значения]}, cells:[[индекс значения по каждой оси (−1 нет)…, маска «посчитано»,
    маска «в очереди» (только не посчитанное: как qd в plan), 0 или код причины «вне формы» (1+индекс в OFF_ROUTES)]]}}; бит i маски — cfg.months[i].
    Один хеш — одна клетка гипотезы; хеш, общий у гипотез (Г-27/Г-89/Г-107: одна клетка), виден у каждой (TK-278, проверка Судьи 17.10)."""
    cfg = cfg or load_config()
    if rows is None:
        rows = plan_rows_cfg(cfg)
        routes = load_routes() if routes is None else routes
    routes = routes or {}
    if have is None:
        import master
        have = master.have_hashes()
        for h, ms in overlay_have(cfg).items():
            have[h] = have.get(h, set()) | ms
    queued = queued_months(cfg) if queued is None else queued
    bit = {m: 1 << i for i, m in enumerate(cfg["months"])}
    ga = grid_axes(rows)
    out, seen = {}, set()
    for r in rows:
        if (r["hyp"], r["hash"]) in seen:
            continue
        seen.add((r["hyp"], r["hash"]))
        axn = list(ga[r["hyp"]])
        o = out.setdefault(r["hyp"], {"axes": ga[r["hyp"]], "cells": []})
        idx = [ga[r["hyp"]][k].index(str(r["axes"][k])) if k in r["axes"] else -1 for k in axn]
        got = have.get(r["hash"], ())
        o["cells"].append(idx + [sum(bit[m] for m in got if m in bit),
                                 sum(bit[m] for m in queued.get(r["hash"], ()) if m in bit and m not in got),
                                 _off_code(routes.get(r["hash"], "cells"))])
    return out


def recent_runs(cfg=None, n=5):
    """Последние запуски формы для чека: [{id, hyps, hm_new, ok}] из <run_dir>/L-*/run.json (+result.json: отказ root-юнита)."""
    cfg = cfg or load_config()
    try:
        names = sorted((x for x in os.listdir(cfg["run_dir"]) if REQ_RE.match(x + ".json")), reverse=True)
    except OSError:
        return []
    out = []
    for nm in names[:n]:
        d = {"id": nm, "hyps": [], "hm_new": 0, "ok": None}
        try:
            r = json.load(open(os.path.join(cfg["run_dir"], nm, "run.json"), encoding="utf-8"))
            d.update(hyps=r.get("hyps", []), hm_new=r.get("hm_new", 0), months=r.get("months", []), ok=True)
        except (OSError, ValueError):
            pass
        try:
            res = json.load(open(os.path.join(cfg["run_dir"], nm, "result.json"), encoding="utf-8"))
            d["ok"] = bool(res.get("ok"))
            d["why"] = res.get("why")
        except (OSError, ValueError):
            pass
        out.append(d)
    return out


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
    rows = plan_rows_cfg(cfg)
    ncell = collections.Counter(r["hyp"] for r in rows)
    hy = [dict(h, ncells=ncell.get(h["id"], 0)) for h in hyp_list()]
    v227, v227_rows = v227_preset()
    return {"months": cfg["months"], "partial_month": "2026-10", "hyps": hy, "axes": grid_axes(rows),
            "presets": {"v227": {"ids": v227, "n": len(v227), "note": f"в таблице spec-v227 {v227_rows} строк «Г-», {len(v227)} различных, не 56"},
                        "months": cfg["months"]},
            "stack": b1_stack(), "bin": bin_info(cfg), "queue": cfg["queue"], "registry_ts": registry_ts()}


def registry_ts():
    """Время снимка Летописи (results.jsonl) в GMT+4."""
    try:
        t = os.stat(os.path.join(REG, "results.jsonl")).st_mtime
    except OSError:
        return None
    return datetime.datetime.fromtimestamp(t, datetime.timezone(datetime.timedelta(hours=4))).strftime("%Y-%m-%d %H:%M")


def letopis(hyp=None, month=None, status=None, cfg=None):
    """Строки вкладки «Летопись»: хеш, гипотеза, оси, знак по месяцам (+/−/пусто), вердикт сита."""
    import cellhash_reg
    cfg = cfg or load_config()
    months = cfg["months"]
    pnl = collections.defaultdict(dict)
    for line in open(os.path.join(REG, "results.jsonl"), encoding="utf-8"):
        if line.strip():
            d = json.loads(line)
            pnl[d["cell_id"]][d["month"]] = (d.get("pnl_usd"), d.get("trades"), d.get("max_dd"), d.get("sum_net_bps"))
    byhash = collections.defaultdict(dict)
    for row in cellhash_reg.build():
        for m, v in pnl.get(row[0], {}).items():
            byhash[row[1]][m] = v
    for r in overlay_rows(cfg):
        byhash[r["cell_id"]][r["month"]] = (r.get("pnl_usd"), r.get("trades"), r.get("max_dd"), r.get("sum_net_bps"))
    mets = hash_metrics_file()
    sieve = {}
    for line in open(os.path.join(REG, "sieve.jsonl"), encoding="utf-8"):
        if line.strip():
            d = json.loads(line)
            sieve.setdefault(d["cell_hash"], []).append(d)
    routes = load_routes()
    out = []
    for r in plan_rows_cfg(cfg):
        if hyp and r["hyp"] != hyp:
            continue
        got = byhash.get(r["hash"], {})
        sq = ["+" if got.get(m, (None,))[0] is not None and got[m][0] > 0 else "-" if m in got else "" for m in months]
        pn = [None if m not in got or got[m][0] is None else round(got[m][0], 2) for m in months]
        nd = [m in got and got[m][0] is None for m in months]   # строка есть, $ не записан (27 хешей R-20261011-002): «н/д», не 0
        bps = [got[m][3] if m in got else None for m in months]
        full = all(sq)
        sv = sieve.get(r["hash"], [])
        sieved = any((x.get("trades") or 0) > 0 or (x.get("days") or 0) > 0 for x in sv)   # в sieve без сделок и суток = не просеяно
        verdict = next((k for k in ("eligible", "plateau", "edge") if any(x.get(k) for x in sv)), "")
        if month and not sq[months.index(month)]:
            continue
        st = "full" if full else "part" if any(sq) else "none"
        if status and status != st and status != verdict:
            continue
        mt = checked_metrics(mets.get(r["hash"]), got)
        rt = routes.get(r["hash"], "cells")
        known = [v for v in pn if v is not None]
        out.append({"hash": r["hash"], "hyp": r["hyp"], "axes": r["axes"], "squares": sq, "status": st, "verdict": verdict, "sieved": sieved,
                    "plateau": any(x.get("plateau") for x in sv), "edge": any(x.get("edge") for x in sv),
                    "off": OFF_LABELS.get(rt) if st == "none" and rt not in COUNTABLE else None,
                    "pnl": pn, "nd": nd, "bps": bps, "sum": round(sum(known), 2) if known else None,
                    "trades": sum(got[m][1] or 0 for m in months if m in got) if got else None,
                    "sharpe": mt and mt["sharpe"], "winrate": mt and mt["winrate"], "overhigh_d": mt and mt["overhigh_d"],
                    "tail_d": mt and mt["tail_d"], "worst_d": mt and mt["worst_d"]})
    return {"months": months, "rows": out, "nhash": len({x["hash"] for x in out}),
            "metrics_n": sum(1 for x in out if x["sharpe"] is not None or x["winrate"] is not None)}


def hyp_view(cfg=None, cov=None):
    """Вкладка «Гипотезы» (TK-271): статусы из реестра автоматически (hyps.view), тексты простым языком — hyp_texts.json."""
    import hyps
    cfg = cfg or load_config()
    cov = coverage(cfg) if cov is None else cov
    hy_of = collections.defaultdict(set)   # хеш -> все гипотезы с этим хешем (Г-27/Г-89/Г-107 — одна клетка)
    for r in plan_rows_cfg(cfg):
        hy_of[r["hash"]].add(r["hyp"])
    ok = collections.defaultdict(set)
    for line in open(os.path.join(REG, "sieve.jsonl"), encoding="utf-8"):
        if line.strip():
            d = json.loads(line)
            if d.get("eligible"):
                for hy in hy_of.get(d["cell_hash"], ()):
                    ok[hy].add(d["cell_hash"])
    rows = [json.loads(l) for l in open(os.path.join(REG, "hypotheses.jsonl"), encoding="utf-8") if l.strip()]
    return hyps.view(rows, cov, cfg["months"], [(r, OFF_LABELS[r]) for r in OFF_ROUTES], hyps.load_texts(), {h: len(v) for h, v in ok.items()})


def hash_metrics_file():
    """metrics.jsonl снимка Летописи (build_metrics.py на calc): {хеш: метрики}; файла нет — {} (на странице прочерки)."""
    try:
        with open(os.path.join(REG, "metrics.jsonl"), encoding="utf-8") as f:
            return {d["hash"]: d for d in map(json.loads, filter(str.strip, f))}
    except OSError:
        return {}


def checked_metrics(mt, got):
    """Метрики хеша — только если closes покрывают ровно месяцы с $ в Летописи и Σ$ closes сходится с pnl_usd канона (±0,02 $ за месяц):
    иначе None (прочерк) — не показываем числа по неполным или расходящимся данным."""
    if not mt or not mt.get("n"):
        return None
    canon = {m: v for m, v in got.items() if v[0] is not None}
    if set(mt["by_month"]) != set(canon):
        return None
    if any(abs(mt["by_month"][m]["pnl"] - v[0]) > 0.02 for m, v in canon.items()):
        return None
    return mt


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
    return ["python3", cfg["alsched"], "submit", "--cls", q["cls"], "--name", run_id, "--cores", str(q["cores"]), "--elastic",
            "--mem", str(q["mem"]), "--max-runtime", str(max_runtime), "--est", str(q["est"]), "--", "env", f"BIN={b['path']}",
            f"SCRIPTS={cfg['scripts_dir']}", f"NX={q['cores']}", "bash", os.path.join(HERE, "run-launch.sh"), run_dir]


def bin_protocols_missing(cfg, p):
    """Протоколы формы с клетками к счёту, которых нет в bin.protocols бинаря (умолч. — основной protocol): ключи П-17 старый бинарь не знает (TK-279)."""
    ok = set(bin_info(cfg).get("protocols") or [cfg["protocol"]])
    todo = {c["hash"] for c in p["cells"] if c["new_months"]}
    return sorted({pr for pr, fr in protocols_of(cfg) if pr not in ok and todo & {r["hash"] for r in plan_rows(pr, fr)}})


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


def request_submit(hyps, months, axes, confirm_bin, cfg=None, p=None, log=print, groups=None):
    """Постановка из формы (юнит alpha-launch без прав на очередь): те же отказы, что у submit, затем запрос атомарно в
    <request_dir>/<run_id>.json; в alsched его ставит root-юнит alpha-launch-submit (process_requests), проверив запрос заново. Один запрос за раз."""
    cfg = cfg or load_config()
    p = p or (plan_groups(groups, cfg=cfg) if groups else plan(hyps, months, axes, cfg=cfg))
    why = precheck(p, False, confirm_bin, cfg, log)
    if why:
        return {"ok": False, "why": why, "plan": p}
    if pending_requests(cfg):
        log("предыдущий запрос ещё не взят в очередь — подождите")
        return {"ok": False, "why": "busy", "plan": p}
    run_id = "L-" + time.strftime("%Y%m%d-%H%M%S")
    path = os.path.join(cfg["request_dir"], run_id + ".json")
    with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as f:
        json.dump({"hyps": list(hyps), "months": list(months), "axes": axes or {}, "groups": groups or [], "confirm_bin": True}, f, ensure_ascii=False)
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
            hyps, months, axes, groups = req.get("hyps") or [], req.get("months") or [], req.get("axes") or {}, req.get("groups") or []
            validate(hyps, months, axes, cfg) if not groups else validate_groups(groups, cfg)
            res = submit(hyps, months, axes, False, bool(req.get("confirm_bin")), cfg, run_cmd=run_cmd, log=lines.append, run_id=rid, groups=groups)
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


def submit(hyps, months, axes=None, dry_run=False, confirm_bin=False, cfg=None, p=None, run_cmd=subprocess.run, log=print, doc=None, run_id=None, groups=None):
    """plan -> К счёту=0 отказ -> потолок -> план-каталог (prepare_plan) -> alsched submit на calc; строка запуска пишется в свой
    каталог сервера <run_dir>/run.json, НЕ в канон Летописи (В-7: канон переносит CEO load-results)."""
    cfg = cfg or load_config()
    p = p or (plan_groups(groups, cfg=cfg) if groups else plan(hyps, months, axes, cfg=cfg))
    if groups:
        hyps, months, axes = sorted({h for g in groups for h in g["hyps"]}), p["months"], {}
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
    miss = bin_protocols_missing(cfg, p)
    if miss:
        log(f"в плане клетки протоколов {', '.join(miss)}, бинарь {b.get('path')} собран без их ключей (config bin.protocols)")
        return {"ok": False, "why": "bin-protocol-needed", "plan": p}
    log("команда: " + shlex.join(cmd))
    log("строк --cells по месяцам: " + ", ".join(f"{m} {n}" for m, n in per_month.items()))
    r1n = {r: sum(len(c["new_months"]) for c in p["cells"] if c["route"] == r) for r in R1_ROUTES}
    if any(r1n.values()):
        log("R1 (хеш×месяц): " + ", ".join(f"{r} {n}" for r, n in r1n.items()))
    if dry_run:
        return {"ok": True, "dry_run": True, "run_id": run_id, "cmd": cmd, "per_month": per_month, "plan": p}
    r = run_cmd(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        log(f"alsched submit rc={r.returncode}: {(r.stderr or r.stdout)[-300:]}")
        return {"ok": False, "why": "alsched-failed", "plan": p}
    row = {"id": run_id, "ts": now_iso(), "kind": "launch", "hyps": sorted(hyps), "months": list(months),
           "axes": axes or {}, "groups": groups or [], "bin": b, "cmd": shlex.join(cmd), "hm_new": p["hm_new"], "new_hm": {c["hash"]: c["new_months"] for c in p["cells"] if c["new_months"]},
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


def runs_view(cfg=None, ps_text="", now=None):
    """Вкладка «Прогоны»: запросы (ещё не взяты юнитом) + каталоги <run_dir>/L-*; свежие сверху. ps_text — вывод `alsched ps` (status()["text"])."""
    import metrics, runs
    cfg = cfg or load_config()
    now = time.time() if now is None else now
    ps = runs.parse_ps(ps_text)
    by_name = {j["name"]: j for j in ps["jobs"]}
    ids = {}
    try:
        for nm in os.listdir(cfg["run_dir"]):
            if REQ_RE.match(nm + ".json"):
                ids[nm] = None
    except OSError:
        pass
    for fn in pending_requests(cfg):
        try:
            ids.setdefault(fn[:-5], json.load(open(os.path.join(cfg["request_dir"], fn), encoding="utf-8")))
        except (OSError, ValueError):
            ids.setdefault(fn[:-5], {})
    have = None
    rows_of = {}   # хеш -> строки плана: один хеш (клетка) бывает у нескольких гипотез — подпись берём из заявленных прогоном
    for r in (plan_rows_cfg(cfg) if ids else ()):
        rows_of.setdefault(r["hash"], []).append(r)

    def hyp_of(h, want):
        c = rows_of.get(h, [])
        return next((r for w in want for r in c if r["hyp"] == w), c[0] if c else {})
    out = []
    for rid in ids:
        d = posixpath.join(cfg["run_dir"], rid)
        in_canon = None
        try:   # «в Летописи» = все хеш×месяц оверлея есть в каноне (master.have_hashes); run_id канона задаёт CEO при переносе — по нему не судим
            pairs = [(r["cell_id"], r["month"]) for r in (json.loads(l) for l in open(os.path.join(d, "overlay.jsonl"), encoding="utf-8") if l.strip())]
            if have is None:
                import master
                have = master.have_hashes()
            in_canon = bool(pairs) and all(m in have.get(h, ()) for h, m in pairs)
        except OSError:
            pass
        rec = runs.run_record(d, rid, now, by_name.get(rid), ids[rid], in_canon, cfg["queue"]["est"])
        met = runs.run_metrics(d) if rec["s"] in ("wait", "done") else {}
        meta = runs._json(os.path.join(d, "run.json")) or {}
        per_hyp = {}
        for h, ms in (meta.get("new_hm") or {}).items():
            per_hyp.setdefault(hyp_of(h, meta.get("hyps") or []).get("hyp", "?"), []).append((h, len(ms)))
        if not meta and ids[rid]:   # запрос ещё не взят юнитом: хеш-мес — по плану того же выбора (как увидела форма)
            try:
                q = ids[rid]
                p = plan_groups(q["groups"], cfg=cfg) if q.get("groups") else plan(q.get("hyps") or [], q.get("months") or [], q.get("axes"), cfg=cfg)
                for c in p["cells"]:
                    if c["new_months"]:
                        per_hyp.setdefault(c["hyp"], []).append((c["hash"], len(c["new_months"])))
                rec["hm"] = p["hm_new"]
            except Exception:   # план не собрался — «—» в строке, вкладка не падает
                pass
        rec["what"] = []
        for hyp, hs in sorted(per_hyp.items()):
            best_h, best = metrics.best_by_sharpe({h: met.get(h) for h, _ in hs})
            rec["what"].append({"hyp": hyp, "hm": sum(n for _, n in hs), "best": None if not best else {
                "axes": hyp_of(best_h, meta.get("hyps") or []).get("axes"), "hash": best_h, **{k: best[k] for k in ("pnl", "sharpe", "winrate", "overhigh_d", "tail_d", "worst_d", "n", "months")}}})
        if not rec["what"]:   # запрос ещё не взят юнитом: хеш-мес неизвестны до плана юнита
            rec["what"] = [{"hyp": h, "hm": None, "best": None} for h in rec["hyps"]]
        rec["best"] = max((w["best"] for w in rec["what"] if w["best"]), key=lambda b: b["sharpe"], default=None)
        out.append(rec)
    out.sort(key=lambda r: r["id"], reverse=True)
    out.sort(key=lambda r: runs.ORDER.get(r["s"], 9))   # как в макете: идёт, очередь, ждёт Летопись, в Летописи, отказ; внутри — свежие сверху
    foreign = [{"name": j["name"], "cores": j["cores"], "state": j["state"], "age_min": j["age_min"]} for j in runs.foreign_jobs(ps, set(ids))]
    return {"runs": out, "foreign": foreign, "cores": {"busy": ps["busy"], "total": ps["total"]}, "bin": bin_info(cfg).get("md5"),
            "weights": {"days": round(runs.W_DAYS, 3), "psim": round(runs.W_PSIM, 3), "src": "проход F1 П-14, TK-231: счёт ≈ 11,5 ч, psim ≈ 50 мин"},
            "metrics_note": "метрики — cap 0 (без потолка позиций), $ с нуля без депозита, по месяцам этого прогона"}


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
        pf = protocols_of(load_config())
        d = build_routes(*pf[0], extra=pf[1:])
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
