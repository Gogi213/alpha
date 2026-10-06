#!/usr/bin/env python3
"""Бэкфилл из тикетов (TK-068): записи лога (.claude/tickets + archive) с признаками прогона (diff 0 / md5 / замер с / ЦП)
→ строки source=backfill-tickets, статус «неполно» (поля извлечены регулярками, проверять по тексту записи)."""
import glob, json, os, re, sys
sys.path.insert(0, os.path.dirname(__file__))
import registry as R

HDR = re.compile(r"^### (\d{4}-\d\d-\d\dT[\d:+]+) (\w+)", re.M)
SIG = re.compile(r"diff 0|md5 ?[:=]? ?[0-9a-f]{6}|\b[0-9a-f]{32}\b|ЦП \d|байт в байт", re.I)
sys.stdout.reconfigure(errors="replace")
TITLES = {}
for _p in glob.glob(os.path.join(R.ROOT, ".claude/tickets/*.md")):
    _m = re.search(r"^title: (.*)$", open(_p, encoding="utf-8").read(), re.M)
    if _m: TITLES[re.match(r"(TK?-\d+)", os.path.basename(_p))[1]] = _m[1]
HOSTS = re.compile(r"89\.163\.242\.211|13\.140\.29\.171|139\.99\.91\.22|Steam Deck|deck@|VPS|сервер[ае]? счёта", re.I)
files = sorted(glob.glob(os.path.join(R.ROOT, ".claude/tickets/*.md")) + glob.glob(os.path.join(R.ROOT, ".claude/tickets/archive/*-log.md")))
rows = []
for p in files:
    tid = re.match(r"(TK?-\d+)", os.path.basename(p))
    if not tid: continue
    tid = tid[1]
    txt = open(p, encoding="utf-8").read()
    title = TITLES.get(tid, "")
    parts = HDR.split(txt)
    for i in range(1, len(parts) - 2, 3):
        ts, role, body = parts[i], parts[i + 1], parts[i + 2].strip()
        if ts < "2026-09-15" or role not in ("engineer", "judge", "researcher"): continue
        if not SIG.search(body): continue
        if len(body) < 80: continue
        md5 = re.findall(r"\b[0-9a-f]{32}\b", body)
        commit = re.findall(r"(?:коммит|commit|·|`)\s*([0-9a-f]{7,8})\b", body)
        host = HOSTS.search(body)
        wall = next((m for m in re.finditer(r"(\d[\d ]*(?:[,.]\d+)?) ?с(?![а-яa-z])", body) if float(m[1].replace(" ", "").replace(",", ".")) >= 10), None)
        cpu = re.search(r"ЦП ?(\d[\d  ]*(?:[,.]\d+)?)", body)
        num = lambda m: m[1].replace(" ", "").replace(" ", "").replace(",", ".") if m else None
        first = re.sub(r"\s+", " ", body)
        row = dict(ts=ts, ticket=tid, kind="speed" if re.search(r"скорост|быстр|бэктест ×|×\d|ЦП|память", title, re.I) else "research",
                   what=f"{title[:80]}: {first[:220]}", outcome=first[:600], machine=host[0] if host else None,
                   binary_md5=md5[0] if md5 else None, commit=commit[0] if commit else None,
                   wall_s=num(wall), cpu_s=num(cpu), judge=None, status="неполно",
                   status_why=f"запись лога {role} {ts[:16]}: поля извлечены регуляркой, свериться с текстом", source="backfill-tickets",
                   note=f"{os.path.relpath(p, R.ROOT).replace(chr(92), '/')}@{ts}")
        rows.append({k: v for k, v in row.items() if v})
keep = [r for r in R.load() if r.get("source") != "backfill-tickets"]
n_by_day = {}
for r in keep:
    m = re.match(r"R-(\d+)-b?(\d+)", r["id"])
    if m: n_by_day[m[1]] = max(n_by_day.get(m[1], 0), int(m[2]))
for r in rows:
    day = re.sub(r"\D", "", r["ts"][:10]); n_by_day[day] = n_by_day.get(day, 0) + 1
    r["id"] = f"R-{day}-t{n_by_day[day]:03d}"
keep += rows
keep.sort(key=lambda r: r["ts"])
with open(R.CANON, "w", encoding="utf-8", newline="\n") as f:
    for r in keep: f.write(json.dumps({k: r[k] for k in R.FIELDS if k in r}, ensure_ascii=False) + "\n")
print(len(rows), "строк из тикетов;", len(keep), "всего")
