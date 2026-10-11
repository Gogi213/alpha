#!/usr/bin/env python3
"""В-227 пакет F (TK-200): раннер Python-механик по закрытиям B1 (М5). Без канонического хеша, вне хеш-счёта Летописи (П-класс П-14 §3.2).
Плагин tools/registry/closures/<имя>.py: NAME, PARAMS {параметр: кортеж допустимых значений}, run(closes, **params) -> [(мс, $), ...].
Ветвления по номеру Г нет: плагин выбирается по --mechanic. Закрытия — JSON portfolio-sim --closes-out: вариант → период → потолок →
[[мс, $], ...]. Выход — CSV «до/после» на (вариант, период, потолок, точка сетки). Точка вне домена плагина — отказ, не молчание.
  closure_mech.py --closes closes.json --mechanic g124 [--grid 'q=90,95;D=1,2'] --out out.csv   (--grid пуст = вся сетка домена)"""
import argparse, csv, importlib, itertools, json, sys


def load_plugin(name):
    mod = importlib.import_module("closures." + name) if name.isidentifier() else None
    if mod is None or not all(hasattr(mod, a) for a in ("NAME", "PARAMS", "run")):
        raise SystemExit(f"плагин {name}: нужны NAME, PARAMS, run")
    return mod


def grid_points(params, spec=""):
    """Декартово произведение значений; spec 'k=v1,v2;…' сужает домен, значение вне домена — отказ."""
    pick = {}
    for part in filter(None, (p.strip() for p in spec.split(";"))):
        k, _, vs = part.partition("=")
        if k not in params:
            raise SystemExit(f"параметр {k} не в домене плагина: {sorted(params)}")
        vals = tuple(type(params[k][0])(v) for v in vs.split(","))
        bad = [v for v in vals if v not in params[k]]
        if bad:
            raise SystemExit(f"{k}: {bad} вне домена {params[k]}")
        pick[k] = vals
    names = sorted(params)
    for combo in itertools.product(*(pick.get(n, params[n]) for n in names)):
        yield dict(zip(names, combo))


def run_all(plugin, closes, spec=""):
    rows = []
    for variant, per in sorted(closes.items()):
        for period, caps in sorted(per.items()):
            for cap, base in sorted(caps.items()):
                base = [(int(m), float(u)) for m, u in (b[:2] for b in base)]
                n0, s0 = len(base), sum(u for _, u in base)
                for pt in grid_points(plugin.PARAMS, spec):
                    fills = plugin.run(base, **pt)
                    rows.append({"mechanic": plugin.NAME, "variant": variant, "period": period, "cap": cap,
                                 "point": ";".join(f"{k}={pt[k]}" for k in sorted(pt)), "n_before": n0,
                                 "sum_before": round(s0, 4), "n_after": len(fills),
                                 "sum_after": round(sum(u for _, u in fills), 4)})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--closes", required=True); ap.add_argument("--mechanic", required=True)
    ap.add_argument("--grid", default=""); ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    rows = run_all(load_plugin(a.mechanic), json.load(open(a.closes, encoding="utf-8")), a.grid)
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["mechanic"], lineterminator="\n")
        w.writeheader(); w.writerows(rows)
    print("строк", len(rows))


if __name__ == "__main__":
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    main()
