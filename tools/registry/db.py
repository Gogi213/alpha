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
    c.commit()
    return c


def load_cells(path, run, hyp, logic):
    """Файл `--cells` («<форма> <набор>» по строке) -> cells.jsonl + run_cells.jsonl, без дублей."""
    have_c = {d["id"] for d in jl("cells.jsonl")}
    have_rc = {(d["run_id"], d["cell_id"]) for d in jl("run_cells.jsonl")}
    nc = nrc = 0
    with open(os.path.join(REGD, "cells.jsonl"), "a", encoding="utf-8", newline="\n") as fc, \
         open(os.path.join(REGD, "run_cells.jsonl"), "a", encoding="utf-8", newline="\n") as fr:
        for ln in open(path, encoding="utf-8"):
            p = ln.split()
            if len(p) != 2:
                continue
            cid = hashlib.sha256(json.dumps({"form": p[0], "set": p[1], "logic": logic}, sort_keys=True,
                                            ensure_ascii=False).encode()).hexdigest()[:16]
            if cid not in have_c:
                have_c.add(cid)
                fc.write(json.dumps({"id": cid, "logic_version": logic, "form": p[0], "set_name": p[1]},
                                    ensure_ascii=False) + "\n")
                nc += 1
            if (run, cid) not in have_rc:
                have_rc.add((run, cid))
                fr.write(json.dumps({"run_id": run, "cell_id": cid, "hyp_id": hyp}, ensure_ascii=False) + "\n")
                nrc += 1
    return nc, nrc
