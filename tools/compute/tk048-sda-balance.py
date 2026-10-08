#!/usr/bin/env python3
"""TK-048: выравнивание чтения бинлогов волны янв+фев по дискам. plan|copy|switch|rollback.
plan  — по pflist считает байты на sdb/sda, выбирает файлы sdb для КОПИИ на sda (одинаковая доля на каждые сутки), пишет план.
copy  — cp + cmp каждого файла плана в /alpha-sda/tk048/bal/<мес>/ (оригиналы не трогает).
switch— атомарно (symlink tmp + rename) подменяет ссылку в study/root-* на копию; прежняя цель пишется в журнал.
rollback — возвращает ссылки по журналу."""
import os, sys, json, shutil, filecmp
OUT = "/data/tk048/bal"; PF = "/data/tk048/pflist-jf59.txt"; DST = "/alpha-sda/tk048/bal"
MON = {"01": "jan", "02": "feb"}
def root(day, sym):
    m = MON[day[5:7]]
    d = f"/data/tk046/{m}/home/alpha/epochs/e-{m}/study/root-{day}"
    return m, d
def files(day, sym):
    m, d = root(day, sym)
    for n in sorted(os.listdir(d)):
        if n.startswith(f"{sym}-{day}.binlog") and not n.endswith(".events"):
            yield m, os.path.join(d, n)
def plan(target_gb):
    rows = []
    for ln in open(PF):
        p = ln.split()
        if len(p) < 2: continue
        sym, day = p[0], p[1]
        try:
            for m, f in files(day, sym):
                r = os.path.realpath(f)
                rows.append((day, m, f, r, os.path.getsize(r), r.startswith("/alpha-sda/")))
        except FileNotFoundError:
            pass
    sdb = sum(r[4] for r in rows if not r[5]); sda = sum(r[4] for r in rows if r[5])
    move = max(0, sdb - target_gb * 1e9); frac = move / sdb if sdb else 0
    pick = []; per = {}
    for r in rows:
        if not r[5]: per.setdefault(r[0], []).append(r)
    for day, lst in per.items():
        want = sum(x[4] for x in lst) * frac; got = 0
        for x in sorted(lst, key=lambda x: -x[4]):
            if got + x[4] / 2 > want: continue
            pick.append(x); got += x[4]
    json.dump([dict(day=x[0], mon=x[1], link=x[2], real=x[3], size=x[4]) for x in pick], open(f"{OUT}/plan.json", "w"))
    print(f"files {len(rows)} sdb_GB {sdb/1e9:.2f} sda_GB {sda/1e9:.2f} target_sdb_GB {target_gb} frac {frac:.3f} pick {len(pick)} copy_GB {sum(x[4] for x in pick)/1e9:.2f}")
def copy():
    P = json.load(open(f"{OUT}/plan.json")); ok = 0
    for x in P:
        d = f"{DST}/{x['mon']}"; os.makedirs(d, exist_ok=True); t = f"{d}/{os.path.basename(x['link'])}"
        if not (os.path.exists(t) and os.path.getsize(t) == x["size"] and filecmp.cmp(x["real"], t, shallow=False)):
            shutil.copyfile(x["real"], t + ".part"); os.replace(t + ".part", t)
            assert filecmp.cmp(x["real"], t, shallow=False), t
        ok += 1
    print("copied_cmp_ok", ok)
def switch():
    P = json.load(open(f"{OUT}/plan.json")); log = []
    for x in P:
        t = f"{DST}/{x['mon']}/{os.path.basename(x['link'])}"
        assert os.path.getsize(t) == x["size"]
        old = os.readlink(x["link"]) if os.path.islink(x["link"]) else None
        assert old is not None, f"не ссылка: {x['link']}"
        tmp = x["link"] + ".tmpln"; os.symlink(t, tmp); os.replace(tmp, x["link"]); log.append(dict(link=x["link"], old=old))
    json.dump(log, open(f"{OUT}/switch-log.json", "w")); print("switched", len(log))
def rollback():
    for x in json.load(open(f"{OUT}/switch-log.json")):
        tmp = x["link"] + ".tmpln"; os.symlink(x["old"], tmp); os.replace(tmp, x["link"])
    print("rolled back")
if __name__ == "__main__":
    os.makedirs(OUT, exist_ok=True); c = sys.argv[1]
    {"plan": lambda: plan(float(sys.argv[2]) if len(sys.argv) > 2 else 13.5), "copy": copy, "switch": switch, "rollback": rollback}[c]()
