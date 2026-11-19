"""TK-618 шаг 2: файл вариантов движка из замороженных клеток `tk618-cells.json` (база S ⊕ патч клетки) и `vid` каждой клетки (как у TK-599: `retitr_full.Req.vid`).
  python short_levers_variants.py <tk599-grids.json> <tk618-cells.json> <выход variants.txt> [<выход vids.json>]
Движок не запускается. Строка файла: `<id клетки> <ключи>`; контрольные строки `S_base`, `S_rf_0.3` — клетки TK-599, сверка побайтно с `out/` TK-599."""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_cells as rc  # noqa: E402
import retitr_full as rf  # noqa: E402


def cell_keys(spec, base, cell):
    return rc.merge_keys(base, rf.patch_str(cell["patch"]))


def build(grids, cells_json):
    spec = rf.Spec.load(grids)
    cj = json.load(open(cells_json, encoding="utf-8"))
    base = cj["base"]
    if rc.canon(base) != rc.canon(spec.bases["S"]):
        raise rf.GuardError("база tk618-cells.json не равна базе S TK-599")
    rows = [("S_base", spec.bases["S"]), ("S_rf_0.3", rc.merge_keys(spec.bases["S"], "rf=0.3"))]
    rows += [(c["id"], cell_keys(spec, spec.bases["S"], c)) for c in cj["cells"]]
    seen = {}
    for cid, keys in rows:
        vid = rf.Req("S", keys, False).vid
        if vid in seen and cid != "B_K1":                      # B_K1 (= rf 0.3) совпадает с контролем S_rf_0.3 по построению
            raise rf.GuardError("клетки %s и %s дают один vid %s" % (seen[vid], cid, vid))
        seen.setdefault(vid, cid)
    return rows


def main(grids, cells_json, out, vids_out=None):
    rows = build(grids, cells_json)
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# П-42 §13.30 (TK-618): замороженные клетки шорта; движок alpha-master md5 a112684a495e61cf5219610e4eecff22; P42_T0 окон — retitr_full.WINDOWS\n")
        for cid, keys in rows:
            f.write("%s %s\n" % (cid, keys))
    if vids_out:
        json.dump({cid: rf.Req("S", keys, False).vid for cid, keys in rows}, open(vids_out, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print("variants", len(rows), out)


if __name__ == "__main__":
    main(*sys.argv[1:5])
