#!/usr/bin/env python3
"""TK-046: дом августа для полного пула v171b на сервере счёта: /data/tk046/aug/home/alpha/epochs/e-aug
(root/, root-<сутки>/, study/{approaches,ax}/D20, sigma240, regime) — симлинки на бинлоги и подходы (старые монеты —
деки/сервера, новые — /data/tk046/aug/study), regime заново по всему пулу. Набор монето-суток = август в вердикте TK-044.
    python3 tk046-home.py [verdict.csv [aug|jan|feb]]      # затем: cd <E>; HOME=<дом> python3 p07-all-month.py aug --merge --bin alpha-tk044k1-new
"""
import csv, glob, os, shutil, subprocess, sys

MON = sys.argv[2] if len(sys.argv) > 2 else "aug"
YM = {"jan": "2026-01", "feb": "2026-02", "aug": "2026-08"}[MON]
VERDICT = sys.argv[1] if len(sys.argv) > 1 else "/data/tk044/final3/verdict.csv"
HOME = f"/data/tk046/{MON}/home"
E = f"{HOME}/alpha/epochs/e-{MON}"
OLDR = f"/data/alpha/epochs/e-{MON}/root"
OLDS = f"/home/deck/alpha/epochs/e-{MON}/study"
NEWS = f"/data/tk046/{MON}/study"
KINDS = ("approaches-{}.csv", "touches-{}.csv", "mids1m-{}.csv", "{}.log")


def link(src, dst):
    if os.path.lexists(dst):
        os.remove(dst)
    os.symlink(src, dst)


new = {}
for ln in open(f"/data/tk046/{MON}-new.txt"):
    s, d, p = ln.split()
    new[(s, d)] = p
rows = []
for r in csv.DictReader(open(VERDICT)):
    if r["day"].startswith(YM):
        rows.append((r["sym"], r["day"]))
print("монето-суток в вердикте (месяц):", len(rows), "новых:", sum(1 for k in rows if k in new))

shutil.rmtree(E, ignore_errors=True)
os.makedirs(f"{E}/root"); os.makedirs(f"{E}/bin"); os.makedirs(f"{E}/study/sigma240"); os.makedirs(f"{E}/study/regime")
for d in ("/home/deck/alpha/bin", "/opt/alpha-compute/bin"):
    for f in os.listdir(d):
        if not os.path.lexists(f"{E}/bin/{f}"):
            link(f"{d}/{f}", f"{E}/bin/{f}")

# instruments: строки старых монет как были, новые — из общего файла
ins = {}
for path in ("/data/tk037/instruments.csv", f"{OLDR}/instruments.csv"):
    with open(path) as f:
        head = f.readline()
        for ln in f:
            ins[ln.split(",")[0]] = ln
with open(f"{E}/root/instruments.csv", "w") as f:
    f.write(head)
    f.writelines(ins[k] for k in sorted(ins))
shutil.copy(f"{OLDR}/session.json", f"{E}/root/session.json")

missing, bysym, byday = [], {}, {}
for s, d in rows:
    src_dir = os.path.dirname(new[(s, d)]) if (s, d) in new else OLDR
    b = new.get((s, d)) or f"{OLDR}/{s}-{d}.binlog"
    if not os.path.exists(b):
        missing.append((s, d, "binlog")); continue
    link(b, f"{E}/root/{s}-{d}.binlog")
    if os.path.exists(b + ".events"):
        link(b + ".events", f"{E}/root/{s}-{d}.binlog.events")
    v = f"{src_dir}/verify-{s}.status"
    if os.path.exists(v) and not os.path.exists(f"{E}/root/verify-{s}.status"):
        shutil.copy(v, f"{E}/root/verify-{s}.status")
    sd = f"{NEWS}/approaches/D20/{d}" if (s, d) in new else f"{OLDS}/approaches/D20/{d}"
    for tag in ("approaches/D20", "ax/D20"):
        os.makedirs(f"{E}/study/{tag}/{d}", exist_ok=True)
        for k in KINDS:
            fn = k.format(s)
            if os.path.exists(f"{sd}/{fn}"):
                link(f"{sd}/{fn}", f"{E}/study/{tag}/{d}/{fn}")
            elif tag == "ax/D20" and k.startswith("approaches"):
                missing.append((s, d, "D20:" + fn))
    sg = f"{NEWS if (s, d) in new else OLDS}/sigma240/sigma-{s}.csv"
    if os.path.exists(sg):
        link(sg, f"{E}/study/sigma240/sigma-{s}.csv")
    elif s not in bysym:
        missing.append((s, d, "sigma"))
    bysym[s] = 1
    byday.setdefault(d, []).append(s)

for d, syms in byday.items():
    R = f"{E}/study/root-{d}"
    os.makedirs(R)
    for s in syms:
        for suf in ("", ".events"):
            p = f"{E}/root/{s}-{d}.binlog{suf}"
            if os.path.lexists(p):
                link(os.path.realpath(p), f"{R}/{s}-{d}.binlog{suf}")
        shutil.copy(f"{E}/root/verify-{s}.status", R) if os.path.exists(f"{E}/root/verify-{s}.status") else missing.append((s, d, "verify"))
    with open(f"{R}/instruments.csv", "w") as f:
        f.write(head)
        f.writelines(ins[s] for s in sorted(set(syms)))
    shutil.copy(f"{E}/root/session.json", R)

for ref in ("BTCUSDT", "ETHUSDT"):
    shutil.copy(f"{OLDS}/regime/ref-{ref}-1m.csv", f"{E}/study/regime/")
for d in sorted(byday):
    subprocess.run(["python3", "/opt/alpha-compute/bin/regime.py", "--day", d, "--touches", "study/ax/D20",
                    "--regime-dir", "study/regime"], cwd=E, check=True, stdout=subprocess.DEVNULL)
print("дней:", len(byday), "монет:", len(bysym), "пропусков:", len(missing))
with open(f"/data/tk046/{MON}/home-missing.txt", "w") as f:
    for m in missing:
        f.write(" ".join(m) + "\n")
