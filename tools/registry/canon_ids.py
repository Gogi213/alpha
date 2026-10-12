#!/usr/bin/env python3
"""Миграция Летописи на канонический хеш (TK-208, В-225): cells.id, run_cells.cell_id, results.cell_id = cellhash.cell_hash.
Карта «старый id -> канон» — cellhash_reg.build() (TK-186). Клетки с равным хешем сливаются в одну (первая по порядку файла),
прежние id — в cells.alias (JSON-список); строки run_cells/results с равным ключом (run, клетка[, месяц]) схлопываются.
Повторный запуск ничего не меняет.  python tools/registry/canon_ids.py [--dry]"""
import json, os, sys
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import cellhash_reg, db


def migrate_rows(cells, run_cells, results, idmap):
    """-> (cells, run_cells, results) на каноне; idmap {старый: канон}."""
    out_c, seen = [], {}
    for d in cells:
        h = idmap.get(d["id"], d["id"])
        al = json.loads(d["alias"]) if isinstance(d.get("alias"), str) else list(d.get("alias") or [])
        if h in seen:
            seen[h]["alias"] = sorted(set(seen[h]["alias"]) | set(al) | ({d["id"]} - {h}))
            continue
        n = {**d, "id": h, "alias": sorted(set(al) | ({d["id"]} - {h}))}
        seen[h] = n
        out_c.append(n)
    def dedup(rows, key):
        got, out = set(), []
        for d in rows:
            d = {**d, "cell_id": idmap.get(d["cell_id"], d["cell_id"])}
            k = key(d)
            if k not in got:
                got.add(k)
                out.append(d)
        return out
    return (out_c, dedup(run_cells, lambda d: (d["run_id"], d["cell_id"])),
            dedup(results, lambda d: (d["run_id"], d["cell_id"], d["month"])))


def write(name, rows):
    with open(os.path.join(db.REGD, name), "w", encoding="utf-8", newline="\n") as f:
        for d in rows:
            f.write(json.dumps(d, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    idmap = {r[0]: r[1] for r in cellhash_reg.build()}
    cs, rc, rs = (db.jl(n) for n in ("cells.jsonl", "run_cells.jsonl", "results.jsonl"))
    nc, nrc, nrs = migrate_rows(cs, rc, rs, idmap)
    print(f"cells {len(cs)}->{len(nc)}; run_cells {len(rc)}->{len(nrc)}; results {len(rs)}->{len(nrs)}")
    if "--dry" not in sys.argv:
        write("cells.jsonl", nc); write("run_cells.jsonl", nrc); write("results.jsonl", nrs)
