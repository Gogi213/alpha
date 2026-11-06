#!/usr/bin/env python3
"""TK-493 w4 (D-01): что изменилось в таблице «один актив» после единой функции классификации (пред-листинг по первичным полям,
приватные базы, явное решение по классу other). Сравнивает прежние asset_map.csv/asset_pairs.csv (копия в --old) с пересобранными (data/xdl9).
Пишет малые таблицы в --out (docs/findings/xdl9-asset-map-prelisting-2026-11-06/): changes_contracts.csv, changes_pairs.csv,
excluded_new.csv, private_bases.csv, summary.json. Торговое окно не читается (вход - готовые таблицы формирования).

  python asset_d01_diff.py --old <каталог со старыми asset_map.csv, asset_pairs.csv> --out <каталог>
"""
import argparse, collections, csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_lib as L, asset_class as AC  # noqa: E402


def read(p):
    with open(p, encoding="utf-8", newline="") as fh:
        return list(csv.DictReader(fh))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    om = {(r["exchange"], r["symbol"]): r for r in read(os.path.join(a.old, "asset_map.csv"))}
    nm = {(r["exchange"], r["symbol"]): r for r in read(os.path.join(L.DATA, "asset_map.csv"))}
    assert om.keys() == nm.keys()
    ch = []
    for k in sorted(nm):
        o, n = om[k], nm[k]
        if (o["role"], o["big"], o["asset_id"], o["leg_c1d2"], o["a1a2b1"]) != (n["role"], n["big"], n["asset_id"], n["leg_c1d2"], n["a1a2b1"]) \
                or (n["role"] == "excluded" and o["reason"] != n["reason"]):
            ch.append([k[0], k[1], n["class"], n["n_valid"], o["role"], n["role"], o["big"], n["big"], o["leg_c1d2"], n["leg_c1d2"],
                       o["a1a2b1"], n["a1a2b1"], o["reason"][:90], n["reason"][:140]])
    cols = ["exchange", "symbol", "class", "n_valid", "role_old", "role_new", "big_old", "big_new", "leg_c1d2_old", "leg_c1d2_new",
            "a1a2b1_old", "a1a2b1_new", "reason_old", "reason_new"]
    with open(os.path.join(a.out, "changes_contracts.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(cols)
        w.writerows(ch)
    # ----- пары
    def pkey(r):
        return (r["ex1"], r["sym1"], r["ex2"], r["sym2"])
    op = {pkey(r): r for r in read(os.path.join(a.old, "asset_pairs.csv"))}
    np_ = {pkey(r): r for r in read(os.path.join(L.DATA, "asset_pairs.csv"))}
    pc = []
    for k in sorted(set(op) | set(np_)):
        o, n = op.get(k), np_.get(k)
        if o is None or n is None:
            pc.append([k[0], k[1], k[2], k[3], "добавлена" if o is None else "убрана", (n or o)["verdict"], (n or o)["scope"], (n or o)["c1d2"],
                       (n or o)["big1"], (n or o)["big2"]])
        elif (o["verdict"], o["c1d2"], o["role1"], o["role2"]) != (n["verdict"], n["c1d2"], n["role1"], n["role2"]):
            pc.append([k[0], k[1], k[2], k[3], "изменена: %s/%s/%s/%s -> %s/%s/%s/%s" % (o["verdict"][:20], o["c1d2"], o["role1"], o["role2"],
                       n["verdict"][:20], n["c1d2"], n["role1"], n["role2"]), n["verdict"], n["scope"], n["c1d2"], n["big1"], n["big2"]])
    with open(os.path.join(a.out, "changes_pairs.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["ex1", "sym1", "ex2", "sym2", "change", "verdict", "scope", "c1d2", "big1", "big2"])
        w.writerows(pc)
    # ----- новые исключённые и приватные базы
    ex_new = [["exchange", "symbol", "class", "n_valid", "pre_listing", "kind", "reason", "was_in_test_as"]]
    for k in sorted(nm):
        n, o = nm[k], om[k]
        if n["role"] == "excluded" and n["reason"].startswith("вне теста: ") and ("пред-листинг" in n["reason"] or "приватная база" in n["reason"]):
            kind = "пред-листинг (первичное поле)" if "пред-листинг" in n["reason"] else "приватная база (ключ)"
            ex_new.append([k[0], k[1], n["class"], n["n_valid"], 1 if kind.startswith("пред") else 0, kind, n["reason"][:200],
                           "" if o["role"] == "excluded" else "%s/%s" % (o["role"], o["big"])])
    with open(os.path.join(a.out, "excluded_new.csv"), "w", encoding="utf-8", newline="") as fh:
        csv.writer(fh, lineterminator="\n").writerows(ex_new)
    pb = AC.derive_private_bases(L.load_inst_all())
    with open(os.path.join(a.out, "private_bases.csv"), "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["base_key", "group", "marked_by"])
        for (k, g), v in sorted(pb.items()):
            w.writerow([k, g, "; ".join(v)])
    # ----- сводка
    roles_o = collections.Counter(r["role"] for r in om.values())
    roles_n = collections.Counter(r["role"] for r in nm.values())
    moves = collections.Counter((c[4], c[5]) for c in ch)
    ex_cnt = collections.Counter(r[0] for r in ex_new[1:])
    usable_o = sum(int(r["c1d2"]) for r in op.values())
    usable_n = sum(int(r["c1d2"]) for r in np_.values())
    summ = dict(roles_old=dict(roles_o), roles_new=dict(roles_n), role_moves={"%s->%s" % k: v for k, v in moves.items()},
                changed_contracts=len(ch), pairs_old=len(op), pairs_new=len(np_), pairs_added=sum(1 for c in pc if c[4] == "добавлена"),
                pairs_removed=sum(1 for c in pc if c[4] == "убрана"), pairs_changed=sum(1 for c in pc if c[4].startswith("изменена")),
                usable_old=usable_o, usable_new=usable_n, excluded_new_by_exchange=dict(ex_cnt),
                excluded_pre_listing=sum(1 for r in ex_new[1:] if r[4] == 1), excluded_private_base=sum(1 for r in ex_new[1:] if r[4] == 0),
                n_valid_gt0_excluded_new=sum(1 for r in ex_new[1:] if int(r[3]) > 0))
    json.dump(summ, open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    print(json.dumps(summ, ensure_ascii=False, indent=1))


if __name__ == "__main__":
    main()
