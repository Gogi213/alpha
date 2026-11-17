"""П-42 §13.14: клетки правила «свежий уровень» (касание в пределах H ч или проторговка K мин) и их строки вариантов для `breakout tick`.

  python3 tools/p42/fresh.py lines   → строки вариантов (контроль b0/b0pk + 17 клеток × pk 0/1) на stdout
"""
import sys

LAT = "lat=4 latx=4"
# оси: значения по порядку (соседние — по соседним значениям)
AX = {"H": [2, 4, 8], "K": [60, 120], "S": [25, 50, 75], "b": [0.3, 0.5, 1]}
MODE_AXES = {"ab": ["H", "K", "S", "b"], "a": ["H"], "b": ["K", "S", "b"]}
BASE = dict(H=4, K=120, S=50, b=0.5)


def _cell(mode, **kw):
    a = dict(mode=mode, H=None, K=None, S=None, b=None)
    a.update(kw)
    return a


# имя → признаки; порядок — порядок списка §13.14
CELLS = {
    "fr_base": _cell("ab", **BASE),
    "fr_h2": _cell("ab", **{**BASE, "H": 2}),
    "fr_h8": _cell("ab", **{**BASE, "H": 8}),
    "fr_k60": _cell("ab", **{**BASE, "K": 60}),
    "fr_m25": _cell("ab", **{**BASE, "S": 25}),
    "fr_m75": _cell("ab", **{**BASE, "S": 75}),
    "fr_b0p3": _cell("ab", **{**BASE, "b": 0.3}),
    "fr_b1": _cell("ab", **{**BASE, "b": 1}),
    "fr_a2": _cell("a", H=2),
    "fr_a4": _cell("a", H=4),
    "fr_a8": _cell("a", H=8),
    "fr_bb": _cell("b", K=120, S=50, b=0.5),
    "fr_bk60": _cell("b", K=60, S=50, b=0.5),
    "fr_bm25": _cell("b", K=120, S=25, b=0.5),
    "fr_bm75": _cell("b", K=120, S=75, b=0.5),
    "fr_bb0p3": _cell("b", K=120, S=50, b=0.3),
    "fr_bb1": _cell("b", K=120, S=50, b=1),
}


def cid(name, pk):
    return name + ("_pk" if pk else "")


def base_id(pk):
    return "b0pk" if pk else "b0"


def neighbors(name):
    """Соседи клетки: тот же вид, отличается одним параметром на соседнее значение оси."""
    a = CELLS[name]
    out = []
    for other, o in CELLS.items():
        if other == name or o["mode"] != a["mode"]:
            continue
        diff = [ax for ax in MODE_AXES[a["mode"]] if a[ax] != o[ax]]
        if len(diff) == 1 and abs(AX[diff[0]].index(a[diff[0]]) - AX[diff[0]].index(o[diff[0]])) == 1:
            out.append(other)
    return out


def fmt(x):
    return "%g" % x


def line(name, pk):
    a = CELLS[name]
    kv = [LAT]
    if pk:
        kv.append("pk=1")
    if a["mode"] in ("ab", "a"):
        kv.append("fh=%s" % fmt(a["H"]))
    if a["mode"] in ("ab", "b"):
        kv += ["fk=%s" % fmt(a["K"]), "fm=%s" % fmt(a["S"]), "fb=%s" % fmt(a["b"])]
    return "%s %s" % (cid(name, pk), " ".join(kv))


def lines():
    out = ["b0 " + LAT, "b0pk %s pk=1" % LAT]
    for pk in (0, 1):
        for name in CELLS:
            out.append(line(name, pk))
    return out


if __name__ == "__main__":
    if sys.argv[1] == "lines":
        print("\n".join(lines()))
