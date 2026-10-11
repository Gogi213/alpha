#!/usr/bin/env python3
# TK-041: полнота данных пула v171b по спискам файлов (бинлоги не читаются).
import os, re, csv, glob, datetime as dt, collections
RX = re.compile(r"^(.+)-(\d{4}-\d{2}-\d{2})\.binlog$")
def sd(b):
    m = RX.match(b)
    return m.groups() if m else (None, None)
T = "/data/tk037"; V = T + "/vroots"; VS = T + "/vroots-split"
LAST = dt.date(2026, 10, 2)
pool = collections.defaultdict(set)
for r in csv.DictReader(open(T + "/pool-v171b.csv")):
    if r["in_pool"] == "1":
        pool[r["sym"]].add(r["day"])
req = {}  # (sym,day) -> pool|carry
for s, ds in pool.items():
    for d in ds:
        req[(s, d)] = "pool"
for s, ds in pool.items():
    for d in ds:
        n = (dt.date.fromisoformat(d) + dt.timedelta(1))
        if n <= LAST and (s, n.isoformat()) not in req:
            req[(s, n.isoformat())] = "carry"
# индекс файлов
files = {}
def idx(root, tag):
    for p in glob.glob(root + "/*/*.binlog") + glob.glob(root + "/*/*.binlog.part"):
        s, d = sd(os.path.basename(p))
        if not s:
            continue
        files.setdefault((s, d), []).append((tag, p))
idx(V, "vroots"); idx(VS, "split")
for p in glob.glob(T + "/v4-days/*.binlog"):
    s, d = sd(os.path.basename(p)); files.setdefault((s, d), []).append(("v4-days", p))
for p in glob.glob("/data/alpha/epochs/e-*/root/*.binlog") + glob.glob(T + "/roots/*/*.binlog"):
    s, d = sd(os.path.basename(p)); files.setdefault((s, d), []).append(("raw", p))
# validate
val = {}
for f in glob.glob(T + "/validate-out/*/files.csv"):
    for r in csv.DictReader(open(f)):
        val[(r["symbol"], r["day"])] = r["status"]
# verify-маркеры
def marker(path):
    try:
        return open(path).read().strip()
    except OSError:
        return None
miss_reason = {}
for fn in ("missing-final.csv", "missing-v171b.csv"):
    try:
        for r in csv.reader(open(T + "/" + fn)):
            if len(r) >= 2 and r[1][:2] == "20":
                miss_reason[(r[0], r[1])] = (fn, r[2] if len(r) > 2 else "")
    except OSError:
        pass
chg = {}  # (sym, day) -> grid, только для монето-месяцев со сменой
for fn, cf in (("tick-change", "tick-change.csv"), ("tick-old", "tick-old.csv")):
    cm = {(r["month"], r["symbol"]) for r in csv.DictReader(open(T + "/" + cf))}
    for r in csv.DictReader(open(T + "/" + fn + ".days.csv")):
        if (r["month"], r["symbol"]) in cm:
            chg[(r["symbol"], r["day"])] = "%s/%s" % (r["tick_e9"], r["step_e9"])
out = []
for (s, d), cat in sorted(req.items()):
    m = d[:7]
    c = files.get((s, d), [])
    tags = [t for t, _ in c]
    vr = [p for t, p in c if t == "vroots" or t == "split"]
    p = ""
    st = "missing"; vstat = ""; valstat = ""; note = ""
    if vr:
        p = vr[0]
        try:
            ok = os.path.getsize(p) > 0
        except OSError:
            ok = False  # битая ссылка
        st = "present" if ok else "broken-link"
        mk = marker(os.path.join(os.path.dirname(p), "verify-%s.status" % s))
        if mk is None:  # split: маркер в каталоге группы или в e-месяц
            mk = marker("%s/e-%s/verify-%s.status" % (V, m, s))
        vstat = mk or "none"
        valstat = val.get((s, d), "none")
    elif c:
        st = "only-" + c[0][0]
        p = c[0][1]
        if c[0][0] == "raw":
            vstat = marker(os.path.join(os.path.dirname(p), "verify-%s.status" % s)) or "none"
            valstat = val.get((s, d), "not-run")
    reason = ""
    if st != "present":
        reason = "|".join("%s:%s" % v for v in [miss_reason.get((s, d), ("", ""))] if v[0]) or "no-record"
    path = os.path.realpath(c[0][1]) if c else ""
    out.append((s, d, cat, m, st, vstat, valstat, ",".join(tags), reason, chg.get((s, d), ""), path))
w = csv.writer(open(T + "/completeness-v171b.csv", "w", newline=""))
w.writerow("sym day cat month file verify validate where reason step_change_grid path".split())
w.writerows(out)
print(len(out), len(pool), sum(len(v) for v in pool.values()))
