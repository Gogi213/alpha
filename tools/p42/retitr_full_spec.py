#!/usr/bin/env python3
"""П-42 §13.24 (TK-599): сетки перетитровки на всём окне — единый источник (оси, клетки, класс оси, соседи, расширения, режим), JSON для заморозки.

  python3 retitr_full_spec.py write <путь.json>   # записать спецификацию (хэш sha256 файла — в §13.24)
  python3 retitr_full_spec.py counts              # число клеток на проход по группам и сторонам
  python3 retitr_full_spec.py table               # таблица осей для §13.24 (markdown)
Клетка = патч ключей движка к текущей конфигурации (база + принятые шаги); значение, равное текущему значению оси, клеткой не является.
Порядок групп и осей внутри группы — порядок в `GROUPS` (регистрируется в §13.24). Класс оси:
  `ordered`    — ≥ 3 значений кроме базы или зарегистрированные расширения; соседи — ближайшие значения сетки кроме текущего; плато: оба соседа Δ ≥ 0,5 Δ клетки;
                 у краевой клетки второго соседа даёт зарегистрированное расширение (`s`: 0,5 и 12; шорт `e`: 0,5; расширение — только сосед, победителем не бывает);
                 без второго соседа клетка пройти не может;
  `categorical`— порог z ≥ 2,5 вместо плато.
Режим статистики (§13.21.1): `sig` — парная Δ на одинаковых сигналах (двойники `_sig`) + вето портфельной Δ ≥ −1·SE; `port` — портфельная Δ.
"""
import hashlib
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import retitr_cells as rc  # noqa: E402

VERSION = "tk599-grids-1"

# значения осей в базах (то, что стоит в строках баз; ключ, которого в строке нет, — значение по умолчанию движка, rc.DEFAULTS)
BASE_VAL = {
    "L": {"s": 5, "ls": 0, "sn": 0, "tt": 0, "tb": 0, "rf": 0.2, "rs": 20, "e": 0.3, "e2": 0, "bm": 0, "rr": 2, "tj": 0,
          "t_high": 5, "q": 50, "natr_min": 0.4, "t1h": 0.5, "t24": 0},
    "S": {"s": 5, "ls": 0, "sn": 0, "tt": 20, "tb": 0.1, "rf": 0.2, "rs": 2, "e": 0.15, "e2": 0.05, "bm": 1, "rr": 2, "tj": 0,
          "t_high": 5, "q": 50, "natr_min": 0.4, "t1h": 0.5, "t24": 0},
}


def _num(x):
    return ("%g" % x)


def cell(axis, name, patch):
    return {"id": "%s_%s" % (axis, name), "patch": {k: _num(v) if not isinstance(v, str) else v for k, v in patch.items()}}


def single(axis, key, values):
    return [cell(axis, _num(v), {key: v}) for v in values]


# (id, группа, режим, класс, ключи оси, значения/клетки по сторонам, порядок значений для упорядоченных, расширения)
def axes(side):
    bv = BASE_VAL[side]
    A = []

    def add(aid, grp, mode, cls, cells, order=None, ext=None):
        # клетки, равные базе по эффективным ключам, не клетки (значение оси совпало с текущим)
        A.append({"axis": aid, "group": grp, "mode": mode, "class": cls, "cells": cells, "order": order or [], "ext": ext or {}})

    # --- группа 1: стоп (режим sig + вето) ---
    s_vals = [1, 1.5, 2, 3, 4, 6, 8]                       # 5 — база (§13.21 E1)
    add("s", 1, "sig", "ordered", single("s", "s", s_vals), [c["id"] for c in single("s", "s", s_vals)],
        {"low": cell("s", "0.5", {"s": 0.5}), "high": cell("s", "12", {"s": 12})})
    add("ls", 1, "sig", "categorical", single("ls", "ls", [0.5, 1]))                      # §13.19
    ns = [("c3", {"sn": 3, "sg": 0}), ("c5", {"sn": 5, "sg": 0}), ("lin", {"sn": 3, "sg": 1, "sb": 1}),
          ("geo", {"sn": 3, "sg": 2, "sr": 1.5, "bm": 1}), ("step", {"sn": 3, "sg": 3, "sd": 0.5, "bm": 1})]   # §13.21 NS
    add("ns", 1, "sig", "categorical", [cell("ns", n, p) for n, p in ns])
    # --- группа 2: выходы ---
    tx = [(5, 0.1), (5, 0.3), (20, 0.1), (20, 0.3)]          # §13.19 tx{T}_x{X}
    add("tx", 2, "sig", "categorical",
        [cell("tx", "%g_%g" % (t, x), {"tt": t, "tb": x}) for t, x in tx if not (t == bv["tt"] and x == bv["tb"])])
    add("rf", 2, "sig", "categorical", single("rf", "rf", [v for v in (0.1, 0.2, 0.3) if v != bv["rf"]]))   # §13.19 f; §13.21 T
    add("rs", 2, "sig", "categorical", single("rs", "rs", [v for v in (2, 20) if v != bv["rs"]]))            # §13.19 t
    # --- группа 3: вход ---
    e_all = (0.05, 0.1, 0.15, 0.2, 0.3) if side == "L" else (0.05, 0.1, 0.15, 0.3)   # лонг: 0,2 — сосед для 0,15 (критик §13.24, 18.11); 0,3 — умолчание
    e_vals = [v for v in e_all if v != bv["e"]]
    e_ext = {"high": cell("e", "0.5", {"e": 0.5})} if side == "S" else None   # шорт: расширение 0,5, чтобы 0,3 могла пройти (критик §13.24)
    add("e", 3, "sig", "ordered", single("e", "e", e_vals), [c["id"] for c in single("e", "e", e_vals)], e_ext)    # §13.19 es
    add("e2", 3, "sig", "categorical", single("e2", "e2", [v for v in (0, 0.05, 0.1) if v != bv["e2"]]))    # §13.19 e2 (0 = выкл.)
    # --- группа 4: безубыток / трейл ---
    add("bm", 4, "sig", "categorical", single("bm", "bm", [v for v in (0, 1, 2, 3) if v != bv["bm"]]))      # §13.19 bm
    add("rr", 4, "sig", "categorical", single("rr", "rr", [v for v in (1, 2, 3) if v != bv["rr"]]))         # §13.21 E2 R
    add("tj", 4, "sig", "categorical", single("tj", "tj", [0.25]))                                          # §13.21 NT
    # --- группа 5: фильтры сетапа и пул ---
    add("t_high", 5, "sig", "categorical", single("t_high", "t_high", [3, 8]))                              # §13.21 T
    add("q", 5, "sig", "categorical", single("q", "q", [25, 75]))                                           # §13.21 T
    nm = [0.2, 0.3, 0.6, 0.8]                                # 0,3 и 0,6 — §13.21 P; 0,2 и 0,8 — только соседи для плато (решение)
    add("natr_min", 5, "port", "ordered", single("natr_min", "natr_min", nm), [c["id"] for c in single("natr_min", "natr_min", nm)])
    add("t1h", 5, "port", "categorical", single("t1h", "t1h", [1, 2]))                                      # §13.21 P, млн
    add("t24", 5, "port", "categorical", single("t24", "t24", [100, 500]))                                  # §13.21 P, млн
    return A


# группа 6: признаки F (движок v21, `--ftab`) — категориальные; тройка fa/fo/fv, fv — квантиль обучающего потока кандидатов, считается в контексте
FEATURES = {
    "L": [("maxmv1", "lo"), ("oi_c24", "hi"), ("loi_turn", "lo")],
    "S": [("tpm15", "lo"), ("cons60", "hi"), ("tpm60", "lo"), ("big1800", "lo"), ("since_born_h", "lo")],
}
EXCLUDED_FEATURES = {"L": ["depth_mean_bp", "depth_min_bp"]}   # источник (bookDepth) покрывает только 14.09–14.10 — не проверяемы на окне
CUT_PCTS = (10, 20, 25)


def spec():
    out = {"version": VERSION, "bases": {"L": rc.BASES["L"], "S": rc.BASES["S"]}, "base_values": BASE_VAL,
           "group_names": {"1": "стоп", "2": "выходы", "3": "вход", "4": "безубыток/трейл", "5": "фильтры и пул", "6": "признаки F"},
           "features": {k: [{"fa": a, "fo": o, "cut_pcts": list(CUT_PCTS)} for a, o in v] for k, v in FEATURES.items()},
           "excluded_features": EXCLUDED_FEATURES, "sides": {}}
    for side in ("L", "S"):
        A = axes(side)
        for a in A:
            ids = [c["id"] for c in a["cells"]]
            if a["class"] == "ordered":
                a["neighbours"] = {}
                lst = a["order"]
                for i, cid in enumerate(lst):
                    nb = []
                    if i > 0:
                        nb.append(lst[i - 1])
                    elif a["ext"].get("low"):
                        nb.append(a["ext"]["low"]["id"])
                    if i < len(lst) - 1:
                        nb.append(lst[i + 1])
                    elif a["ext"].get("high"):
                        nb.append(a["ext"]["high"]["id"])
                    a["neighbours"][cid] = nb
                    # клетка проходит плато только с двумя соседями
                a["order"] = [c for c in lst if c in ids]
        out["sides"][side] = A
    return out


def counts():
    sp = spec()
    for side in ("L", "S"):
        row = {}
        for a in sp["sides"][side]:
            row.setdefault(a["group"], 0)
            row[a["group"]] += len(a["cells"])
        f = len(sp["features"][side]) * len(CUT_PCTS)
        row[6] = f
        ext = sum(1 for a in sp["sides"][side] for k in ("low", "high") if a["ext"].get(k))
        print(side, "клеток на проход по группам", row, "всего", sum(row.values()), "(+ до %d расширений: s, шорт e)" % ext)


def table():
    sp = spec()
    for side in ("L", "S"):
        print("\n**%s** (база: `%s`)\n" % ("Лонг" if side == "L" else "Шорт", sp["bases"][side]))
        print("| группа | ось | класс | режим | клетки (патч ключей) | соседи / расширения |")
        print("|---|---|---|---|---|---|")
        for a in sp["sides"][side]:
            cells = "; ".join("`%s`" % " ".join("%s=%s" % kv for kv in c["patch"].items()) for c in a["cells"])
            nb = ""
            if a["class"] == "ordered":
                nb = "порядок: %s" % " < ".join(list(c["patch"].values())[0] for c in a["cells"])
                if a["ext"]:
                    nb += "; расширения: " + ", ".join("%s `%s`" % (k, " ".join("%s=%s" % kv for kv in v["patch"].items())) for k, v in a["ext"].items() if v)
            print("| %d | `%s` | %s | %s | %s | %s |" % (a["group"], a["axis"], "упорядоченная" if a["class"] == "ordered" else "категориальная", a["mode"], cells, nb))
        feats = ", ".join("`%s` (%s)" % (x["fa"], x["fo"]) for x in sp["features"][side])
        print("| 6 | признаки F | категориальная | port | %s × отсечки 10 / 20 / 25 %% хвоста потока кандидатов обучающих месяцев | — |" % feats)


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "write":
        path = sys.argv[2]
        txt = json.dumps(spec(), ensure_ascii=False, indent=1, sort_keys=False) + "\n"
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(txt)
        print(path, hashlib.sha256(txt.encode("utf-8")).hexdigest())
    elif cmd == "counts":
        counts()
    elif cmd == "table":
        table()
    else:
        print(__doc__)
        sys.exit(2)
