#!/usr/bin/env python3
"""TK-367: удаление данных Bybit янв–июн 2026 по списку, одобренному Судьёй (review tk367-delete-list-2026-10-23).

  tk367-delete.py plan  — раскрывает delete-list-full.tsv в явный список delete-paths.lst (kind, path, bytes, month)
                          и проверяет его; ничего не удаляет.
  tk367-delete.py run   — удаляет по delete-paths.lst (сначала /srv/alpha-sda, затем /data), без шелл-глобов:
                          строки `…/*[MM]` раскрыты в файлы из files.lst (родитель + месяц).

Условия Судьи: 7 строк cargo `/tgt-*/release/` вычеркнуты; EXCLUDE-классы не берутся; в путях ни одной даты 2026-07..12.
Каталоги — shutil.rmtree без перехода по ссылкам; ссылки — unlink; защищённые корни не трогаются.
"""
import collections
import csv
import json
import os
import re
import shutil
import subprocess
import sys
import time

W = "/data/tk367"
FULL = f"{W}/delete-list-full.tsv"
PATHS = f"{W}/delete-paths.lst"
DONE = f"{W}/delete-done.tsv"
PROG = f"{W}/delete-progress.log"
SUMMARY = f"{W}/delete-summary.json"
FILES_LST = [f"{W}/inv2a/files.lst", f"{W}/inv2b/files.lst"]

PROTECTED = [
    "/data/registry", "/data/launch/registry", "/data/local-backup", "/data/lost+found",
    "/data/sched", "/data/board", "/data/progress",
]
ROOTS = ("/srv/alpha-sda/", "/data/")  # порядок удаления: sda5 → /data (Судья)
DATE = re.compile(r"2026-0([1-6])(?!\d)")
TOKEN = re.compile(r"(?<![a-z])(jan|feb|mar|apr|may|jun)(?![a-z])")
MON = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06"}
LATE = re.compile(r"2026-(0[7-9]|1[0-2])")
CARGO = re.compile(r"/tgt-[^/]+/release/")


def month_of(name):
    m = DATE.search(name)
    if m:
        return "0" + m.group(1)
    m = TOKEN.search(name.lower())
    return MON[m.group(1)] if m else None


def log(msg):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    with open(PROG, "a") as f:
        f.write(line + "\n")


def df(path):
    s = os.statvfs(path)
    return {"mount": path, "size": s.f_blocks * s.f_frsize, "used": (s.f_blocks - s.f_bfree) * s.f_frsize,
            "avail": s.f_bavail * s.f_frsize}


def protected(path):
    return any(path == p or path.startswith(p + "/") or p.startswith(path + "/") for p in PROTECTED)


def plan():
    files_by = collections.defaultdict(list)
    for fl in FILES_LST:
        with open(fl) as f:
            for line in f:
                p, _, sz = line.rstrip("\n").rpartition("\t")
                mo = month_of(os.path.basename(p))
                if mo:
                    files_by[(os.path.dirname(p), mo)].append((p, int(sz)))
    out, skipped_excl, skipped_cargo, errors = [], 0, 0, []
    with open(FULL, newline="") as f:
        rd = csv.reader(f, delimiter="\t")
        next(rd)
        for path, kind, b, n, mo, klass in rd:
            if klass.startswith("EXCLUDE"):
                skipped_excl += 1
                continue
            if CARGO.search(path):
                skipped_cargo += 1
                continue
            b, n = int(b), int(n)
            if kind == "files":
                parent = path[: -len("/*[%s]" % mo)]
                lst = files_by.get((parent, mo), [])
                if len(lst) != n or sum(s for _, s in lst) != b:
                    errors.append(f"files-mismatch {path}: список {len(lst)}/{sum(s for _, s in lst)} строка {n}/{b}")
                    continue
                for p, s in lst:
                    out.append(("file", p, s, mo))
            else:
                if month_of(os.path.basename(path)) != mo:
                    errors.append(f"name-month {path} {mo}")
                    continue
                out.append((kind, path, b, mo))
    for kind, p, s, mo in out:
        if not p.startswith(ROOTS):
            errors.append(f"root {p}")
        if protected(p):
            errors.append(f"protected {p}")
        if LATE.search(p):
            errors.append(f"late-date {p}")
        if not month_of(os.path.basename(p)):
            errors.append(f"basename-no-month {p}")
    dirs = sorted(p for k, p, _, _ in out if k in ("dir", "link"))
    nested = 0
    for a, b in zip(dirs, dirs[1:]):
        if b.startswith(a + "/"):
            nested += 1
            errors.append(f"nested {a} ⊃ {b}")
    if len({p for _, p, _, _ in out}) != len(out):
        errors.append("duplicate paths")
    out.sort(key=lambda r: (not r[1].startswith(ROOTS[0]), r[1]))
    with open(PATHS, "w") as f:
        for kind, p, s, mo in out:
            f.write(f"{kind}\t{p}\t{s}\t{mo}\n")
    tot = collections.Counter()
    for kind, p, s, mo in out:
        tot["srv" if p.startswith(ROOTS[0]) else "data"] += s
    print(json.dumps({"entries": len(out), "excluded_class": skipped_excl, "cargo_dropped": skipped_cargo,
                      "gb_srv_sda5": round(tot["srv"] / 1e9, 2), "gb_data": round(tot["data"] / 1e9, 2),
                      "gb_total": round((tot["srv"] + tot["data"]) / 1e9, 2), "nested": nested,
                      "errors": len(errors)}, ensure_ascii=False))
    for e in errors[:30]:
        print("ERR", e)
    return 0 if not errors else 1


def checks():
    """Контрольные счётчики Судьи (до/после): spot/ob/SOLUSDT и июль–окт в tk037raw."""
    r = {}
    ob = "/srv/alpha-sda/tk305/spot/ob/SOLUSDT"
    r["ob_SOLUSDT_files"] = len(os.listdir(ob)) if os.path.isdir(ob) else None
    raw = "/data/tk037raw"
    names = os.listdir(raw) if os.path.isdir(raw) else []
    r["tk037raw_total"] = len(names)
    r["tk037raw_jul_oct"] = sum(1 for n in names if LATE.search(n))
    return r


def remove_entry(kind, p):
    """Возвращает статус: ok | missing | skip-*."""
    parent = os.path.dirname(p)
    rp = os.path.realpath(parent)
    if not rp.startswith(ROOTS) or protected(rp) or protected(p):
        return "skip-parent"
    if kind == "file":
        try:
            os.unlink(p)
            return "ok"
        except FileNotFoundError:
            return "missing"
    if os.path.islink(p):
        os.unlink(p)
        return "ok"
    if not os.path.lexists(p):
        return "missing"
    errs = []
    shutil.rmtree(p, onerror=lambda fn, path, exc: errs.append(f"{path}: {exc[1]}"))
    if errs:
        log("ERR " + "; ".join(errs[:3]))
        return "error"
    return "ok"


def run():
    if plan() != 0:
        log("план не прошёл проверки — отказ")
        return 2
    entries = [l.rstrip("\n").split("\t") for l in open(PATHS)]
    before = {"df": [df("/data"), df("/srv/alpha-sda")], "checks": checks()}
    log("ДО " + json.dumps(before, ensure_ascii=False))
    stats = collections.Counter()
    freed = collections.Counter()
    t0 = time.time()
    with open(DONE, "a") as done:
        for i, (kind, p, s, mo) in enumerate(entries):
            st = remove_entry(kind, p)
            stats[st] += 1
            if st == "ok":
                freed["srv" if p.startswith(ROOTS[0]) else "data"] += int(s)
            done.write(f"{st}\t{kind}\t{p}\t{s}\t{mo}\n")
            if i % 500 == 0:
                done.flush()
                log(f"{i}/{len(entries)} {dict(stats)} освобождено по списку: "
                    f"srv {freed['srv']/1e9:.1f} ГБ data {freed['data']/1e9:.1f} ГБ, {time.time()-t0:.0f} с")
    after = {"df": [df("/data"), df("/srv/alpha-sda")], "checks": checks()}
    log("ПОСЛЕ " + json.dumps(after, ensure_ascii=False))
    summ = {"entries": len(entries), "stats": dict(stats), "freed_by_list_gb": {k: round(v / 1e9, 2) for k, v in freed.items()},
            "before": before, "after": after, "seconds": round(time.time() - t0)}
    with open(SUMMARY, "w") as f:
        json.dump(summ, f, ensure_ascii=False, indent=1)
    log("ИТОГ " + json.dumps(summ["stats"]) + f" за {summ['seconds']} с")
    return 0 if not (stats["error"] or stats["skip-parent"]) else 1


if __name__ == "__main__":
    sys.exit({"plan": plan, "run": run}[sys.argv[1]]())
