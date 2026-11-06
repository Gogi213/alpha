#!/usr/bin/env python3
"""TK-493 w4 (D-01 / хвост F5): явное решение «в тест / вне» для контрактов класса `other` 8 минутных бирж -> asset_other_resolution.csv.
ТОЛЬКО формирование 01.10-14.10 (ряды data/xdl9/asset/*.f32); торговое окно не читается.

Правило (назначено без Судьи, подтвердить; новых чисел нет - пороги те же, что у «одного актива», П-37 §4.2 / params stab_threshold, n_ret_min):
  контракт `other` (после пред-листинга/приватных баз/датированных - они не сюда) идёт «в тест», если ровно у одного УКРУПНЁННОГО класса П-37 есть
  контракт с тем же ключом имени на другой бирже, чей вердикт против него «одно» (n_ret >= n_ret_min и S <= stab_threshold), т.е. тот же
  актив опознан измерением, а класс - полем другой биржи (equity/etf/crypto/...). Иначе «вне»: биржа класс не назвала, измерение его не
  унаследовало. Контракт без валидных минут формирования - «вне» (класс ни на что не влияет).
Запуск: python asset_other_resolve.py   (нужен numpy; перезаписывает tools/compute/statarb/xdl9/asset_other_resolution.csv)
"""
import csv, json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import asset_lib as L, asset_cands as A  # noqa: E402

N_RET_MIN = 300          # как asset_table.N_RET_MIN (назначено без Судьи в F5)


def main():
    cal = json.load(open(os.path.join(L.DATA, "calib_summary.json"), encoding="utf-8"))
    stab = cal["S"]["mid"]
    rows, C = L.load_contracts(lazy=True)
    L.RESOLUTION = {}                        # решения строим с нуля: other -> вне, пока измерение не скажет иное
    A.build(rows, C)
    by_key = {}
    for d in rows:
        if d["big"] != L.OUT_OF_TEST and d["n_valid"] > 0:
            by_key.setdefault(d["key"], []).append(d)
            for k2 in d["keys_extra"]:
                by_key.setdefault(k2, []).append(d)
    out = []
    for d in rows:
        if d["out_kind"] != "other_out":
            continue
        if d["n_valid"] == 0:
            out.append([d["exchange"], d["symbol"], "вне", "", "нет валидных минут на формировании (n_valid=0): класс ни на что не влияет", ""])
            continue
        votes = {}            # укрупнённый класс П-37 -> [метки контрагентов]; raw: сырой класс instruments.csv -> число контрагентов
        raw = {}
        weak = []
        for o in by_key.get(d["key"], []) + [x for k2 in d["keys_extra"] for x in by_key.get(k2, [])]:
            if o["exchange"] == d["exchange"] or o["i"] == d["i"]:
                continue
            s = L.pair_stats(C[d["i"]], C[o["i"]])
            tag = "%s:%s S=%.3f n=%d" % (o["exchange"], o["symbol"], s["S"], s["n_ret"])
            if s["n_ret"] >= N_RET_MIN and s["S"] <= stab:
                votes.setdefault(L.BIG[o["class"]], []).append(tag)
                raw[o["class"]] = raw.get(o["class"], 0) + 1
            else:
                weak.append(tag)
        if len(votes) == 1:
            big = next(iter(votes))
            cls = sorted(raw.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]      # самый частый сырой класс контрагентов
            out.append([d["exchange"], d["symbol"], "в тест", cls,
                        "тот же актив, что контракт(ы) %s на другой бирже (сырые классы %s; S <= %.5f, n_ret >= %d)"
                        % (big, "/".join("%s:%d" % kv for kv in sorted(raw.items())), stab, N_RET_MIN),
                        "; ".join(votes[big][:4])])
        elif len(votes) > 1:
            out.append([d["exchange"], d["symbol"], "вне", "", "тот же актив, но у контрагентов разные укрупнённые классы %s - класс не определён" % "/".join(sorted(votes)),
                        "; ".join(t for v in votes.values() for t in v[:2])])
        else:
            why = "биржа класс не назвала, ни один контракт с тем же ключом имени не прошёл «одно»"
            if not by_key.get(d["key"]):
                why = "биржа класс не назвала, контрактов с тем же ключом имени на других биржах нет"
            out.append([d["exchange"], d["symbol"], "вне", "", why, "; ".join(weak[:4])])
    out.sort(key=lambda r: (r[0], r[1]))
    p = os.path.join(L.HERE, "asset_other_resolution.csv")
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["exchange", "symbol", "decision", "class_new", "basis", "evidence"])
        w.writerows(out)
    n_in = sum(1 for r in out if r[2] == "в тест")
    print("other_out: %d, в тест %d, вне %d -> %s" % (len(out), n_in, len(out) - n_in, p))
    for r in out:
        if r[2] == "в тест":
            print("  в тест:", r[0], r[1], "->", r[3], "|", r[5])


if __name__ == "__main__":
    main()
