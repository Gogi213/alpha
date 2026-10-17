"""TK-293: объединение реестра git и снимка calc (/data/launch/registry) по ключам таблиц.
python3 tk293-registry-union.py <calc-каталог> <git-каталог docs/registry> <выход-каталог>
Ключи: runs.id; results (run_id,cell_id,month); cells/run_cells — строка целиком; metrics.hash (calc главнее: строка прошла
check, а git-версия — прежняя); plan_cells (protocol,fraction,hyp,cell_hash); hyp_axes (hyp,protocol,version,param); params.name;
rules (protocol,version,key). Порядок: строки git, затем только-calc. Одинаковый ключ с разным текстом — отказ, кроме metrics."""
import json, os, sys

CALC, GIT, OUT = sys.argv[1:4]
KEYS = {
    "runs.jsonl": lambda d: d["id"],
    "results.jsonl": lambda d: (d["run_id"], d["cell_id"], d["month"]),
    "metrics.jsonl": lambda d: d["hash"],
    "plan_cells.jsonl": lambda d: (d["protocol"], d["fraction"], d["hyp"], d["cell_hash"]),
    "hyp_axes.jsonl": lambda d: (d["hyp"], d["protocol"], d["version"], d["param"]),
    "params.jsonl": lambda d: d["name"],
    "rules.jsonl": lambda d: (d["protocol"], d["version"], d["key"]),
}


def rows(p):
    return [l.rstrip("\n") for l in open(p, encoding="utf-8") if l.strip()] if os.path.exists(p) else []


os.makedirs(OUT, exist_ok=True)
for f in sorted(set(os.listdir(CALC)) | set(os.listdir(GIT))):
    if not f.endswith(".jsonl"):
        continue
    g, c = rows(os.path.join(GIT, f)), rows(os.path.join(CALC, f))
    kf = KEYS.get(f)
    if kf is None:   # без ключа: объединение строк, порядок git, затем calc
        out = list(dict.fromkeys(g + c))
    else:
        cm = {kf(json.loads(l)): l for l in c}
        out, seen = [], set()
        for l in g:
            k = kf(json.loads(l))
            if k in cm and cm[k] != l and f != "metrics.jsonl":
                sys.exit(f"{f}: ключ {k} — разный текст git/calc")
            l = cm.get(k, l) if f == "metrics.jsonl" else l
            if k not in seen:
                seen.add(k)
                out.append(l)
        for l in c:
            k = kf(json.loads(l))
            if k not in seen:
                seen.add(k)
                out.append(l)
    open(os.path.join(OUT, f), "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
    print(f, "git", len(g), "calc", len(c), "→", len(out))
