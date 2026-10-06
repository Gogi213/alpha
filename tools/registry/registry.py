#!/usr/bin/env python3
"""Реестр прогонов alpha (TK-068). Канон — docs/registry/runs.jsonl (одна строка = один прогон, git);
SQLite data/registry.sqlite собирается из него (`build`) для запросов. Команды: add | build | find | show | stats | import-auto."""
import argparse, json, os, sqlite3, sys, datetime, re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
CANON = os.path.join(ROOT, "docs", "registry", "runs.jsonl")
DB = os.path.join(ROOT, "data", "registry.sqlite")
STATUS = ("боевой", "проба", "недействителен", "неполно")
FIELDS = [  # колонка -> смысл
    "id", "ts", "ticket", "protocol", "kind", "what", "hyp", "data_pool", "period", "coins", "gate", "epochs",
    "binary_md5", "commit", "flags", "machine", "wall_s", "cpu_s", "result_path", "outcome", "judge", "status",
    "status_why", "source", "note", "config", "config_status"]
# kind: research (гипотезы/семьи/клетки) | speed (замер скорости) | data (импорт/проверка данных) | other


def load():
    rows = []
    if os.path.exists(CANON):
        with open(CANON, encoding="utf-8") as f:
            for ln in f:
                ln = ln.strip()
                if ln:
                    rows.append(json.loads(ln))
    return rows


def now():
    tz = datetime.timezone(datetime.timedelta(hours=4))
    return datetime.datetime.now(tz).strftime("%Y-%m-%dT%H:%M:%S+04:00")


def next_id(rows, ts):
    day = re.sub(r"\D", "", ts[:10])
    n = sum(1 for r in rows if r["id"].startswith(f"R-{day}-")) + 1
    return f"R-{day}-{n:03d}"


def append(row):
    row = {k: v for k, v in row.items() if v not in (None, "")}
    rows = load()
    row.setdefault("ts", now())
    row.setdefault("id", next_id(rows, row["ts"]))
    if any(r["id"] == row["id"] for r in rows):
        sys.exit(f"registry: id {row['id']} уже есть")
    st = row.get("status")
    if st and st not in STATUS:
        sys.exit(f"registry: status ∈ {STATUS}")
    os.makedirs(os.path.dirname(CANON), exist_ok=True)
    with open(CANON, "a", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps({k: row[k] for k in FIELDS if row.get(k) not in (None, "")}, ensure_ascii=False) + "\n")
    return row["id"]


def build():
    rows = load()
    os.makedirs(os.path.dirname(DB), exist_ok=True)
    if os.path.exists(DB):
        os.remove(DB)
    c = sqlite3.connect(DB)
    c.execute("create table runs (" + ",".join(f'"{k}" text' for k in FIELDS) + ")")
    c.execute("create unique index runs_id on runs(id)")
    for r in rows:
        c.execute(f"insert into runs values ({','.join('?' * len(FIELDS))})", [None if r.get(k) is None else str(r[k]) for k in FIELDS])
    c.commit()
    return c, len(rows)


def show_row(r, full=False):
    head = f"{r['id']}  {r.get('ts','')[:16]}  [{r.get('status','?')}]  {r.get('ticket','')} {r.get('protocol','')}"
    print(head)
    print(f"  что: {r.get('what','')}")
    if full:
        for k in FIELDS:
            if k not in ("id", "ts", "what") and r.get(k) not in (None, ""):
                print(f"  {k}: {r[k]}")
    else:
        for k in ("data_pool", "period", "result_path", "outcome", "judge"):
            if r.get(k):
                print(f"  {k}: {r[k]}")


def main():
    ap = argparse.ArgumentParser(prog="registry")
    sp = ap.add_subparsers(dest="cmd", required=True)
    a = sp.add_parser("add", help="дописать прогон")
    for k in FIELDS:
        if k not in ("id", "ts") or k == "ts":
            a.add_argument("--" + k.replace("_", "-"), dest=k)
    a.add_argument("--id", dest="id")
    sp.add_parser("build", help="собрать data/registry.sqlite из канона")
    f = sp.add_parser("find", help="поиск по всем полям (подстрока, без регистра); несколько слов = И")
    f.add_argument("words", nargs="+")
    f.add_argument("--status")
    f.add_argument("--full", action="store_true")
    s = sp.add_parser("show"); s.add_argument("id")
    sp.add_parser("stats")
    ia = sp.add_parser("import-auto", help="влить строки автозаписи benchrun (jsonl) в канон")
    ia.add_argument("file")
    ns = ap.parse_args()
    if ns.cmd == "add":
        row = {k: getattr(ns, k, None) for k in FIELDS}
        if not row.get("what"):
            sys.exit("registry: нужен --what")
        row["status"] = row.get("status") or "боевой"
        print(append(row))
    elif ns.cmd == "build":
        _, n = build(); print(f"{n} строк → {DB}")
    elif ns.cmd == "find":
        ws = [w.lower() for w in ns.words]
        for r in load():
            t = json.dumps(r, ensure_ascii=False).lower()
            if all(w in t for w in ws) and (not ns.status or r.get("status") == ns.status):
                show_row(r, ns.full)
    elif ns.cmd == "show":
        for r in load():
            if r["id"] == ns.id:
                show_row(r, True)
    elif ns.cmd == "stats":
        rows = load(); print(len(rows), "прогонов")
        for key in ("kind", "status", "source"):
            cnt = {}
            for r in rows:
                cnt[r.get(key, "—")] = cnt.get(r.get(key, "—"), 0) + 1
            print(key, cnt)
    elif ns.cmd == "import-auto":
        have = {r.get("note") for r in load()}
        n = 0
        mdir = os.path.join(os.path.dirname(os.path.abspath(ns.file)), "runs")
        for ln in open(ns.file, encoding="utf-8"):
            if not ln.strip():
                continue
            a = json.loads(ln)
            key = f"auto:{a['host']}:{a['start']}:{a['pid']}"
            if key in have:
                continue
            row = {"ts": a["start"], "kind": "speed" if a.get("cls") == "wave" else "other", "what": a["cmd"],
                   "machine": a["host"], "wall_s": a["wall_s"], "status": "проба" if a["rc"] else "боевой",
                   "status_why": f"rc={a['rc']}" if a["rc"] else "", "source": "benchrun-auto", "note": key,
                   "config_status": "неполон: манифест не снят"}
            mp = os.path.join(mdir, a.get("manifest", "-"))
            if os.path.exists(mp):
                m = json.load(open(mp, encoding="utf-8"))
                for k in ("host", "cls", "start", "t0"):
                    m.pop(k, None)
                row["config"] = m
                row["config_status"] = "полный (команда, env, файлы и входы по sha)"
                row["flags"] = m["cmdline"][:300]
                if m.get("git"): row["commit"] = m["git"]
                bm = [b["md5"] for b in m.get("binaries", {}).values()]
                if bm: row["binary_md5"] = ",".join(bm)
            append(row)
            n += 1
        print(n, "строк добавлено")


if __name__ == "__main__":
    main()
