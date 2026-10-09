#!/usr/bin/env python3
"""Летопись, TK-089: сверка таблицы гипотез с выходами на диске сервера счёта (R2 — r2-summary.csv, R1 — файлы клеток).
Вход: data/tk089/r2-summary-server.csv (ssh cat /data/tk065/r2-summary.csv) и data/tk089/r1-cells-ls.txt
(ssh: for m in feb..oct; do echo "## $m"; ls /data/tk064/r1/cells/$m; done). Выход: docs/findings/letopis-check-2026-10-08.{md,csv}."""
import csv, collections, hashlib, os, re
ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
F = os.path.join(ROOT, "docs", "findings")
D = os.path.join(ROOT, "data", "tk089")
tab = {r["id"]: r for r in csv.DictReader(open(os.path.join(F, "letopis-hypotheses-2026-10-08.csv"), encoding="utf-8"), delimiter=";")}
BASE_T = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
BASE_1 = "ladder3x0..0.0409sw2-pct2-1to1-14400-ttl1800"
SUF = [("-pyre", "Г-92"), ("-pynw", "Г-93"), ("-pyeat", "Г-94"), ("-conv", "Г-119"), ("-half", "Г-114"), ("-tsl", "Г-117"), ("-pyfresh", "Г-87")]
def hyp_of(form):
    if form.startswith("ladder3x0..0.0409sw3"): return "Г-95"
    for s, h in SUF:
        if s in form: return h
srv = os.path.join(D, "r2-summary-server.csv"); repo = os.path.join(F, "r2-pool-summary-2026-10-07.csv")
sha = lambda p: hashlib.sha256(open(p, "rb").read()).hexdigest()
same = sha(srv) == sha(repo)
cells = collections.defaultdict(set); months = collections.defaultdict(set)
for r in csv.DictReader(open(srv, encoding="utf-8")):
    h = hyp_of(r["form"])
    if h and int(r["n_fills"]) > 0: cells[h].add((r["set"], r["form"])); months[h].add(r["month"][5:])
rows = []
for h in sorted(cells):
    t = tab[h]; want_m = t["месяцы_в_Летописи"]; got_m = ",".join(sorted(months[h]))
    want_c = int(re.match(r"(\d+)", t["сетка"]).group(1))
    rows.append((h, "R2 /data/tk065/r2-summary.csv", f"месяцы {want_m}; клеток {want_c}", f"месяцы {got_m}; клеток {len(cells[h])}",
                 "совпало" if want_m == got_m and want_c == len(cells[h]) else "РАСХОЖДЕНИЕ"))
ls = open(os.path.join(ROOT, "docs", "registry", "files", "tk089-r1-cells-ls-2026-10-08.txt"), encoding="utf-8").read().split("## ")[1:]
r1 = collections.defaultdict(lambda: collections.defaultdict(set))
MON = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10"}
for blk in ls:
    mo, *files = blk.split()
    for fn in files:
        m = re.match(r"g(\d+)-.*\.csv$", fn)
        if m: r1["Г-%02d" % int(m.group(1))][MON[mo]].add(fn)
for h in sorted(r1):
    t = tab.get(h)
    if not t or not t["месяцы_отбор_R1_исходов_нет"]: continue
    got_m = ",".join(sorted(r1[h])); want_m = t["месяцы_отбор_R1_исходов_нет"]
    got_c = len({fn for fs in r1[h].values() for fn in fs})
    want_c = t["клеток_R1"]
    rows.append((h, "R1 /data/tk064/r1/cells/<мес>", f"месяцы {want_m}; клеток {want_c}", f"месяцы {got_m}; клеток {got_c}",
                 "совпало" if want_m == got_m and str(got_c) == str(want_c) else "РАСХОЖДЕНИЕ"))
with open(os.path.join(F, "letopis-check-2026-10-08.csv"), "w", encoding="utf-8", newline="") as f:
    w = csv.writer(f, delimiter=";"); w.writerow(["id", "источник на диске", "в таблице", "на диске", "итог"]); w.writerows(rows)
ok = sum(r[4] == "совпало" for r in rows)
with open(os.path.join(F, "letopis-check-2026-10-08.md"), "w", encoding="utf-8") as f:
    f.write(f"# Летопись: сверка таблицы гипотез с диском сервера (08.10, TK-089)\n\nR2: `r2-summary.csv` сервера и `r2-pool-summary-2026-10-07.csv` репо — {'побайтно равны (sha256)' if same else 'РАЗЛИЧАЮТСЯ'}. "
            f"Сверено {len(rows)} гипотез, совпало {ok}. Генератор — `python tools/registry/archive/letopis_check.py` (вход — data/tk089).\n\n| id | источник | в таблице | на диске | итог |\n|---|---|---|---|---|\n")
    for r in rows: f.write("| " + " | ".join(r) + " |\n")
print(len(rows), ok, same)
