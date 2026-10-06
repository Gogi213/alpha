#!/usr/bin/env python3
"""Бэкфилл реестра (TK-068): пересобирает строки source=backfill-* из JOURNAL.md, runs.csv (prereg/pilot/amendment),
таблицы П в EXPERIMENTS.md. Строки других источников (add, benchrun-auto) не трогает."""
import csv, json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
import registry as R

def iso(d, t=""):
    m = re.match(r"(\d\d)\.(\d\d)", d.strip())
    if not m: return None
    y = "2026"; hh = re.search(r"(\d\d):(\d\d|xx)", t or d)
    return f"{y}-{m[2]}-{m[1]}T{hh[1] if hh else '00'}:{hh[2] if hh and hh[2] != 'xx' else '00'}:00+04:00"

rows = []
# JOURNAL.md
for ln in open(os.path.join(R.ROOT, "docs/research/JOURNAL.md"), encoding="utf-8"):
    if not ln.startswith("| ") or ln.startswith("| дата") or ln.startswith("|---"): continue
    c = [x.strip() for x in ln.strip().strip("|").split("|")]
    if len(c) < 6 or not iso(c[0]): continue
    prot = c[1].replace("П-", "P-")
    step = c[2]
    kind = "research" if re.search(r"счёт|прогон", step + c[3][:80]) else "other"
    rows.append(dict(ts=iso(c[0]), protocol=prot if prot != "—" else "", kind=kind, what=f"{step}: {c[3][:300]}",
                     result_path=c[4].strip("`"), outcome=c[3][:500], status="неполно",
                     status_why="строка журнала исследований: данные/бинарник/машина не извлечены", source="backfill-journal",
                     note="JOURNAL.md"))
# runs.csv
with open(os.path.join(R.ROOT, "docs/plan/runs.csv"), encoding="utf-8", newline="") as f:
    for r in csv.DictReader(f):
        if r["kind"] == "confirmatory": continue
        rows.append(dict(ts=r["ts_utc"].replace("Z", "+00:00")[:25], kind="research", what=f"{r['kind']} {r['symbol']}: {r['detail'][:300]}",
                         outcome=r["detail"][:500], status="неполно", status_why="runs.csv (пополнялся до 22.09): " + r["kind"],
                         source="backfill-runs", note="docs/plan/runs.csv"))
# EXPERIMENTS П-таблица
for ln in open(os.path.join(R.ROOT, "docs/plan/EXPERIMENTS.md"), encoding="utf-8"):
    m = re.match(r"\| (П-\d+[^|]*)\| (\d\d\.\d\d) \| ([^|]*)\| ([^|]*)\| ([^|]*)\| ([^|]*)\| ([^|]*)\|", ln)
    if not m: continue
    rows.append(dict(ts=iso(m[2]), protocol=m[1].strip().replace("П-", "P-"), kind="research", what=m[3].strip(), outcome=f"{m[4].strip()} | {m[6].strip()}"[:500],
                     result_path=m[7].strip().strip("`"), status="неполно", status_why="таблица П: данные/бинарник/машина не извлечены",
                     source="backfill-experiments", note="EXPERIMENTS.md"))
keep = [r for r in R.load() if not str(r.get("source", "")).startswith("backfill-")]
for i, r in enumerate(sorted(rows, key=lambda x: x["ts"] or "")):
    r["note"] = f"{r.get('note','')}#{i}"
    keep.append(r)
# id заново для backfill
seen = {}
out = []
for r in keep:
    r = {k: v for k, v in r.items() if v not in (None, "")}
    if "id" not in r or str(r.get("source", "")).startswith("backfill-"):
        day = re.sub(r"\D", "", r["ts"][:10]); seen[day] = seen.get(day, 0) + 1
        r["id"] = f"R-{day}-b{seen[day]:03d}"
    out.append(r)
out.sort(key=lambda r: r["ts"])
with open(R.CANON, "w", encoding="utf-8", newline="\n") as f:
    for r in out:
        f.write(json.dumps({k: r[k] for k in R.FIELDS if k in r}, ensure_ascii=False) + "\n")
print(len(out), "строк в каноне")
