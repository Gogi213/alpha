#!/usr/bin/env python3
"""TK-303: метки плато sieve.jsonl на объединённой сетке F1∪F2(A+B)∪R1 (вердикт Судьи 20.10).
Правило — master.sieve (rules протокола, соседи по осям из plan_cells всех фракций); годность — из строк sieve (без изменений).
  python tk303-plateau-union.py [--protocol P-15] [--write]   # без --write — только отчёт"""
import argparse, collections, itertools, json, os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
REG = os.path.join(ROOT, "docs", "registry")


def jl(name):
    return [json.loads(l) for l in open(os.path.join(REG, name), encoding="utf-8") if l.strip()]


def fval(x):
    s = str(x).strip().replace(",", ".")
    m = re.fullmatch(r"(-?[\d.]+)/([\d.]+)", s)
    try:
        return float(m.group(1)) / float(m.group(2)) if m else float(s)
    except ValueError:
        return None


ap = argparse.ArgumentParser()
ap.add_argument("--protocol", default="P-15")
ap.add_argument("--write", action="store_true")
a = ap.parse_args()

rl = [r for r in jl("rules.jsonl") if r["protocol"] == a.protocol]
ver = max(r["version"] for r in rl)
rules = {r["key"]: fval(r["value"]) for r in rl if r["version"] == ver}
types = {p["name"]: p.get("axis_type", "numeric") for p in jl("params.jsonl")}
sv = jl("sieve.jsonl")

grid = collections.defaultdict(dict)   # hyp -> {hash: axes}
for r in jl("plan_cells.jsonl"):
    if r["protocol"] == a.protocol:
        grid[r["hyp"]][r["cell_hash"]] = json.loads(r["axes"])

elig = {}   # (hyp, hash) -> годна (строка с посчитанными сделками/сутками; F2 побеждает F1)
for r in sorted((r for r in sv if r["protocol"] == a.protocol), key=lambda r: r["fraction"]):
    if r.get("trades") or r.get("days"):
        elig[(r["hyp"], r["cell_hash"])] = int(r["eligible"])

lab = {}   # (hyp, hash) -> (plateau, edge)
for hyp, cells in grid.items():
    names = list(next(iter(cells.values())))
    num = [x for x in names if types.get(x, "numeric") != "categorical"]
    cat = [x for x in names if types.get(x, "numeric") == "categorical"]
    pos = {x: {v: i for i, v in enumerate(sorted({str(c[x]) for c in cells.values()}, key=fval))} for x in num}
    at = {(tuple(str(c[x]) for x in cat), tuple(pos[x][str(c[x])] for x in num)): h for h, c in cells.items()}
    for h, c in cells.items():
        ck = tuple(str(c[x]) for x in cat)
        ix = tuple(pos[x][str(c[x])] for x in num)
        pl = ed = 0
        if elig.get((hyp, h)) and num:
            nbs, full = set(), 3 ** len(num) - 1
            for d in itertools.product((-1, 0, 1), repeat=len(num)):
                if not any(d):
                    continue
                j = []
                for x, i, di in zip(num, ix, d):
                    n = len(pos[x])
                    jj = i + di
                    if types.get(x) == "circular":
                        jj %= n
                    elif not 0 <= jj < n:
                        jj = None
                    j.append(jj)
                if None not in j and (ck, tuple(j)) in at and tuple(j) != ix:
                    nbs.add(at[(ck, tuple(j))])
            good = sum(elig.get((hyp, x), 0) for x in nbs)
            n = len(nbs)
            if len(num) == 1:
                ed, pl = int(n == 1), int(good == n and n >= 1)
            else:
                ed = int(n < full)
                pl = int(good >= rules["plateau_min_nb"] if n == full else good * 2 > n)
        lab[(hyp, h)] = (pl, ed)

chg = collections.defaultdict(list)
for r in sv:
    k = (r["hyp"], r["cell_hash"])
    if r["protocol"] != a.protocol or k not in lab or not r["eligible"]:
        continue
    pl, ed = lab[k]
    if (r["plateau"], r["edge"]) != (pl, ed):
        chg[(r["hyp"], r["fraction"])].append((r["cell_hash"], (r["plateau"], r["edge"]), (pl, ed)))
        r["plateau"], r["edge"] = pl, ed
for (hyp, fr), v in sorted(chg.items()):
    print(f"{hyp} F{fr}: изменено {len(v)}")
    for h, o, n in v:
        print(f"   {h} {json.dumps(grid[hyp].get(h), ensure_ascii=False)} плато/край {o} -> {n}")
print("итого изменено строк:", sum(len(v) for v in chg.values()), "| версия правила", ver)
if a.write and chg:
    with open(os.path.join(REG, "sieve.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for r in sv:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print("sieve.jsonl переписан")
