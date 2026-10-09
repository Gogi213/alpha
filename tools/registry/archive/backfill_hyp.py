#!/usr/bin/env python3
"""Бэкфилл hypotheses.jsonl (из пула гипотез 25.09) и verdicts.jsonl (из docs/research/reviews/*.md) — TK-068.
Пересобирает оба файла целиком (канон — источники). Запуск: python tools/registry/archive/backfill_hyp.py"""
import glob, json, os, re

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", ".."))
REG = os.path.join(ROOT, "docs", "registry")
POOL = os.path.join(ROOT, "docs", "research", "hypothesis-pool-2026-09-25.md")
REV = os.path.join(ROOT, "docs", "research", "reviews")


def hypotheses():
    out, fam = {}, None
    for ln in open(POOL, encoding="utf-8"):
        if ln.startswith("## "):
            fam = None
        if not ln.startswith("| Г-"):
            continue
        c = [x.strip() for x in ln.strip().strip("|").split(" | ")]
        if len(c) < 8 or not re.fullmatch(r"Г-\d+[а-я]?", c[0]) or c[0] in out:
            continue
        out[c[0]] = {"id": c[0], "family": c[2], "title": c[1],
                     "ext": {"mechanism": c[3], "source": c[4], "data": c[5], "cost": c[6], "status_pool_0925": c[7],
                             "priority": c[10] if len(c) > 10 else None, "pool_doc": os.path.basename(POOL)}}
    return list(out.values())


def verdicts():
    out = []
    for p in sorted(glob.glob(os.path.join(REV, "*.md"))):
        name = os.path.basename(p)
        txt = open(p, encoding="utf-8").read()
        m = re.search(r"^\*\*Итог:?\*?\*?:?\s*(.+)$", txt, re.M)
        head = txt.splitlines()[0] if txt else ""
        v = m.group(1).strip() if m else ""
        low = v.lower()
        if not v:
            low = txt[:1500].lower()
        verdict = ("вернуть" if re.search(r"вернуть|возвра", low[:300]) else
                   "принято" if re.search(r"принят|принимаю|согласен", low[:300] if v else low) else
                   "отклонено" if re.search(r"отклон", low[:300]) else "см. текст")
        dm = re.search(r"(\d{4}-\d{2}-\d{2})", name)
        pm = re.match(r"(P-\d+)", name)
        hyps = sorted(set(re.findall(r"Г-\d+[а-я]?", head)))
        out.append({"id": "V-" + name[:-3], "hyp_id": hyps[0] if len(hyps) == 1 else None, "judge": "judge",
                    "verdict": verdict, "review_path": "docs/research/reviews/" + name,
                    "ts": dm.group(1) if dm else None,
                    "note": (head.lstrip("# ")[:160] + " — " + v[:240]).strip(" —")})
    return out


def dump(name, rows):
    with open(os.path.join(REG, name), "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    print(name, len(rows))


if __name__ == "__main__":
    dump("hypotheses.jsonl", hypotheses())
    dump("verdicts.jsonl", verdicts())
