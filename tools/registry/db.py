"""SQLite-слой реестра (TK-068): миграции, сборка из каноничных jsonl, загрузка клеток. Канон — docs/registry/*.jsonl (git)."""
import hashlib, json, os, re, sqlite3, datetime

HERE = os.path.dirname(os.path.abspath(__file__))
REGD = os.path.join(HERE, "..", "..", "docs", "registry")
DB = os.path.join(HERE, "..", "..", "data", "registry.sqlite")
MIG = os.path.join(HERE, "migrations")
HYPRE = re.compile(r"Г-\d+[а-я]?|R[12]-\d+")


def _now():
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=4))).strftime("%Y-%m-%dT%H:%M:%S+04:00")


def migrate(c):
    c.execute("create table if not exists schema_version (version integer primary key, name text, applied_at text)")
    have = {r[0] for r in c.execute("select version from schema_version")}
    for fn in sorted(os.listdir(MIG)):
        m = re.match(r"(\d+)_.*\.sql$", fn)
        if m and int(m.group(1)) not in have:
            c.executescript(open(os.path.join(MIG, fn), encoding="utf-8").read())
            c.execute("insert into schema_version values (?,?,?)", (int(m.group(1)), fn, _now()))
    c.commit()


def jl(name):
    p = os.path.join(REGD, name)
    if not os.path.exists(p):
        return []
    return [json.loads(x) for x in open(p, encoding="utf-8") if x.strip()]


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _ins(c, table, d):
    c.execute(f"insert or replace into {table} ({','.join(d)}) values ({','.join('?' * len(d))})",
              [json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v for v in d.values()])


def build(rows):
    """Пересобрать data/registry.sqlite: миграции + runs-строки + hypotheses/cells/run_cells/results/verdicts из jsonl."""
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)
    c = sqlite3.connect(DB)
    migrate(c)
    for h in jl("hypotheses.jsonl"):
        _ins(c, "hypotheses", h)
    for r in rows:
        cfg = r.get("config") if isinstance(r.get("config"), dict) else None
        _ins(c, "runs", {
            "id": r["id"], "ts": r.get("ts", ""), "ticket": r.get("ticket"), "protocol": r.get("protocol"),
            "kind": r.get("kind"), "what": r.get("what"), "machine": r.get("machine"), "wall_s": _num(r.get("wall_s")),
            "cpu_s": _num(r.get("cpu_s")), "result_path": r.get("result_path"), "outcome": r.get("outcome"),
            "judge": r.get("judge"), "status": r.get("status"), "status_why": r.get("status_why"),
            "source": r.get("source"), "note": r.get("note"), "cmdline": (cfg or {}).get("cmdline"),
            "config_status": r.get("config_status"), "config": cfg})
        for h in sorted(set(HYPRE.findall(" ".join(str(r.get(k, "")) for k in ("hyp", "what", "protocol"))))):
            c.execute("insert or ignore into run_hypotheses values (?,?)", (r["id"], h))
        inp = (cfg or {}).get("inputs", {})

        def sha_of(sub):
            for k, v in inp.items():
                if sub in k:
                    return v.get("sha256") if isinstance(v, dict) else v
        if inp or any(r.get(k) for k in ("data_pool", "period", "coins", "gate", "epochs")):
            c.execute("insert into run_data (run_id,pool,pool_sha,verdict_sha,epochs,period,coins) values (?,?,?,?,?,?,?)",
                      (r["id"], r.get("data_pool"), sha_of("pool"), sha_of("verdict") or r.get("gate"),
                       r.get("epochs"), r.get("period"), r.get("coins")))
        mds = [m for m in str(r.get("binary_md5", "")).split(",") if m]
        for m in mds or ([None] if r.get("commit") or r.get("flags") else []):
            c.execute("insert into run_binaries values (?,?,?,?)", (r["id"], m, r.get("commit"), r.get("flags")))
    for t, name in (("cells", "cells.jsonl"), ("run_cells", "run_cells.jsonl"), ("results", "results.jsonl"),
                    ("verdicts", "verdicts.jsonl")):
        for d in jl(name):
            _ins(c, t, d)
    c.execute("insert or ignore into run_hypotheses select distinct run_id, hyp_id from run_cells where hyp_id is not null")
    c.commit()
    return c


def parse_cmd(text):
    """Из текста команды bounce-grid: глобальные опции и `--set имя:k=v,…` -> (глобальное, {набор: {k: v}})."""
    g = {}
    for k in ("queue-model", "h3-mode", "deadline-secs", "hold-step", "sigma-from", "exit-form", "median-rtt-ns",
              "stop-form", "take-form", "entry-form", "side", "entry-ttl-secs"):
        vals = re.findall(r"--" + k + r"[ =]+([^\s\\]+)", text)
        if vals:
            g[k] = vals[0] if len(vals) == 1 else vals
    sets = {}
    for name, body in re.findall(r"--set[ =]+['\"]?([^:\s'\"]+):([^\s'\"]*)", text):
        sets[name] = {k: v for k, _, v in (x.partition("=") for x in body.split(",") if x)}
    return g, sets


def _pct_bps(form):
    m = re.fullmatch(r"pct([\d.]+)", form) if isinstance(form, str) else None
    return float(m.group(1)) * 100 if m else None


def load_cells(path, run, hyp, logic, cmd_file=None):
    """Файл `--cells` («<форма> <набор>» по строке) -> cells.jsonl + run_cells.jsonl, без дублей.
    cmd_file — текст команды/скрипта: типизированные колонки и params берутся из его опций и `--set`."""
    g, sets = parse_cmd(open(cmd_file, encoding="utf-8").read()) if cmd_file else ({}, {})
    have_c = {d["id"] for d in jl("cells.jsonl")}
    have_rc = {(d["run_id"], d["cell_id"]) for d in jl("run_cells.jsonl")}
    nc = nrc = 0
    with open(os.path.join(REGD, "cells.jsonl"), "a", encoding="utf-8", newline="\n") as fc, \
         open(os.path.join(REGD, "run_cells.jsonl"), "a", encoding="utf-8", newline="\n") as fr:
        for ln in open(path, encoding="utf-8"):
            p = ln.split()
            if len(p) != 2:
                continue
            one = lambda k: g.get(k) if isinstance(g.get(k), str) else None
            cell = {"logic_version": logic, "form": p[0], "set_name": p[1],
                    "stop_bps": _pct_bps(one("stop-form")), "take_bps": _pct_bps(one("take-form")),
                    "deadline_s": _num(one("deadline-secs")), "wall_exit": one("exit-form"), "set_form": p[1],
                    "latency_ms": (_num(one("median-rtt-ns")) or 0) / 1e6 or None, "queue": one("queue-model"),
                    "h3_mode": one("h3-mode"), "sigma": _num(one("sigma-from")), "hold_step": one("hold-step"),
                    "params": {"set": sets.get(p[1], {}), "cmd": {k: v for k, v in g.items() if k not in
                               ("queue-model", "h3-mode", "deadline-secs", "hold-step", "sigma-from", "exit-form",
                                "median-rtt-ns")}}}
            cell = {k: v for k, v in cell.items() if v not in (None, {}, "")}
            cid = hashlib.sha256(json.dumps(cell, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
            if cid not in have_c:
                have_c.add(cid)
                fc.write(json.dumps({"id": cid, **cell}, ensure_ascii=False) + "\n")
                nc += 1
            if (run, cid) not in have_rc:
                have_rc.add((run, cid))
                fr.write(json.dumps({"run_id": run, "cell_id": cid, "hyp_id": hyp}, ensure_ascii=False) + "\n")
                nrc += 1
    return nc, nrc


def load_results(dirp, run, logic):
    """Каталог агрегатов agg_tk040.py (<мес>.csv: группа,набор,форма,месяц,…) -> results.jsonl (+cells, run_cells)."""
    import csv, glob
    cells = {(d.get("form"), d.get("set_name")): d["id"] for d in jl("cells.jsonl")}
    have_rc = {(d["run_id"], d["cell_id"]) for d in jl("run_cells.jsonl")}
    have_r = {(d["run_id"], d["cell_id"], d["month"]) for d in jl("results.jsonl")}
    n = dup = 0
    op = lambda name: open(os.path.join(REGD, name), "a", encoding="utf-8", newline="\n")
    with op("cells.jsonl") as fc, op("run_cells.jsonl") as fr, op("results.jsonl") as fo:
        for fp in sorted(glob.glob(os.path.join(dirp, "*.csv"))):
            for r in csv.DictReader(open(fp, encoding="utf-8")):
                sn = r["group"] + "/" + r["set"]
                key = (r["form"], sn)
                cid = cells.get(key)
                if cid is None:
                    cell = {"logic_version": logic, "form": r["form"], "set_name": sn}
                    cid = hashlib.sha256(json.dumps(cell, sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:16]
                    cells[key] = cid
                    fc.write(json.dumps({"id": cid, **cell}, ensure_ascii=False) + "\n")
                if (run, cid) not in have_rc:
                    have_rc.add((run, cid))
                    fr.write(json.dumps({"run_id": run, "cell_id": cid, "hyp_id": None}, ensure_ascii=False) + "\n")
                if (run, cid, r["month"]) in have_r:
                    dup += 1
                    continue
                have_r.add((run, cid, r["month"]))
                ext = {k: r[k] for k in ("group", "days", "symdays", "sum_net_bps", "n_signals", "n_stop", "n_take", "n_trail", "n_deadline")}
                fo.write(json.dumps({"run_id": run, "cell_id": cid, "month": r["month"], "trades": int(r["n_fills"]),
                                     "ext": ext}, ensure_ascii=False) + "\n")
                n += 1
    return n, dup


GROUP_HYP = ((r"p02-h9e899-market", "Г-86"), (r"p02-h10-", "Г-105"), (r"p07a-", "Г-85а"), (r"p07b-", "Г-85б"),
             (r"p07m-", "В-104"), (r"p05-", "П-05"))


def _wjl(name, rows):
    with open(os.path.join(REGD, name), "w", encoding="utf-8", newline="\n") as f:
        for d in rows:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


def bind_hyp(run):
    """hyp_id клеток прогона по группе (из results.ext.group): П-02 → Г-86/Г-105, П-07 → Г-85а/б (B1 = p07b-base), p07m → В-104, П-05 → П-05;
    заодно типизированные колонки клеток из имени формы (стоп pctN → bps, дедлайн, TTL входа, ladder)."""
    grp = {}
    for d in jl("results.jsonl"):
        if d["run_id"] == run:
            grp[d["cell_id"]] = (d.get("ext") or {}).get("group", "")
    rc = jl("run_cells.jsonl")
    nb = 0
    for d in rc:
        if d["run_id"] == run and d["cell_id"] in grp:
            for pat, h in GROUP_HYP:
                if re.match(pat, grp[d["cell_id"]]):
                    d["hyp_id"] = h; nb += 1
                    break
    _wjl("run_cells.jsonl", rc)
    cells = jl("cells.jsonl")
    nt = 0
    for c in cells:
        if c["id"] not in grp:
            continue
        f = c.get("form") or ""
        m = re.search(r"-pct([\d.]+)-", f) or re.match(r"pct([\d.]+)-", f)
        if m:
            c["stop_bps"] = float(m.group(1)) * 100
        m = re.search(r"-(\d{4,6})-ttl(\d+)", f)
        if m:
            c["deadline_s"] = float(m.group(1))
            p = c.setdefault("params", {}); p["entry_ttl_s"] = int(m.group(2))
        m = re.search(r"-(at|behind|before)-tr", f) or re.match(r"(at|behind|before)-tr", f)
        if m:
            c["wall_exit"] = m.group(1)
        c.setdefault("params", {})["group"] = grp[c["id"]]
        nt += 1
    _wjl("cells.jsonl", cells)
    return nb, nt


def load_usd(dirp, run):
    """usd-<мес>.csv (agg_tk040_usd.py) -> results.pnl_usd / max_dd (+ notional в ext)."""
    import csv, glob
    cells = {(d.get("form"), d.get("set_name")): d["id"] for d in jl("cells.jsonl")}
    res = jl("results.jsonl")
    idx = {(d["run_id"], d["cell_id"], d["month"]): d for d in res}
    n = 0
    for fp in sorted(glob.glob(os.path.join(dirp, "usd-*.csv"))):
        for r in csv.DictReader(open(fp, encoding="utf-8")):
            d = idx.get((run, cells.get((r["form"], r["group"] + "/" + r["set"])), r["month"]))
            if d is None:
                continue
            d["pnl_usd"] = float(r["pnl_usd"]); d["max_dd"] = float(r["max_dd_usd"])
            d.setdefault("ext", {})["notional_usd"] = r["notional_usd"]
            d["ext"]["n_rounds"] = r["n_rounds"]
            n += 1
    _wjl("results.jsonl", res)
    return n
