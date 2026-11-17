"""Собирает отчёт из шаблона и полных таблиц docs/findings/arb-holding-divergences-2026-11-16-tables-{strict,tol}.md: в шаблоне {{T:вариант|начало заголовка|колонки (индексы через запятую)|строки (первая колонка)|переименование k=имя;…}}.
Запуск из корня репо: python3 tools/arb_stats/hold_mkdoc.py <шаблон.md> <выход.md>"""
import os, re, sys
BASE = os.environ.get("HOLD_TABLES_BASE", "docs/findings/arb-holding-divergences-2026-11-16-")
SRC = {}
for v in ("strict", "tol"):
    blocks = open(f"{BASE}tables-{v}.md", encoding="utf-8").read().split("\n### ")
    blocks[0] = blocks[0].removeprefix("### ")
    SRC[v] = {b.split("\n")[0]: [l for l in b.split("\n")[1:] if l.startswith("|")] for b in blocks}

def find(v, prefix):
    hits = [h for h in SRC[v] if h.startswith(prefix)]
    if len(hits) != 1:
        raise SystemExit(f"header {prefix!r} in {v}: {len(hits)} hits")
    return SRC[v][hits[0]]

def table(v, prefix, cols=None, rows=None, rename=None):
    lines = find(v, prefix)
    hdr = [c.strip() for c in lines[0].strip("|").split("|")]
    body = [[c.strip() for c in l.strip("|").split("|")] for l in lines[2:]]
    if rows:
        body = [r for r in body if r[0] in rows]
    idx = list(range(len(hdr))) if cols is None else [hdr.index(c) if isinstance(c, str) else c for c in cols]
    names = [(rename or {}).get(i, (rename or {}).get(hdr[i], hdr[i])) for i in idx]
    out = ["| " + " | ".join(names) + " |", "|" + "---|" * len(idx)]
    out += ["| " + " | ".join(r[i] for i in idx) + " |" for r in body]
    return "\n".join(out)

def render(tpl):
    def rep(m):
        parts = m.group(1).split("|")
        v, prefix = parts[0], parts[1]
        cols = [int(x) if x.strip().isdigit() else x.strip() for x in parts[2].split(",")] if len(parts) > 2 and parts[2] else None
        rows = parts[3].split(",") if len(parts) > 3 and parts[3] else None
        ren = {}
        if len(parts) > 4 and parts[4]:
            for kv in parts[4].split(";"):
                k, nm = kv.split("=")
                ren[int(k) if k.strip().isdigit() else k.strip()] = nm
        return table(v, prefix, cols, rows, ren)
    return re.sub(r"\{\{T:(.*?)\}\}", rep, tpl)

if __name__ == "__main__":
    tpl = open(sys.argv[1], encoding="utf-8").read()
    open(sys.argv[2], "w", encoding="utf-8").write(render(tpl))
