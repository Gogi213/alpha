#!/usr/bin/env python3
"""В-227 пакет F (TK-200): раннер Python-механик по закрытиям B1 (М5). Без канонического хеша, вне хеш-счёта Летописи (П-класс П-14 §3.2).
Плагин tools/registry/closures/<имя>.py: NAME, PARAMS {параметр: кортеж допустимых значений}, KIND, SOURCES, run(data, ctx, **params)
(решение Судьи 12.10, design-v227 §2.4): KIND='closes' — data = закрытия B1, выход [(мс, $)]; 'keep' — data = строки signals.csv + исход
rounds.csv, выход = набор ключей keep (цепочка busy-replay → expo → psim — на calc); 'rounds' — data = rounds.csv, выход {ключ: (exit_ns, exit_px)}.
ctx — ленивые загрузчики по месяцу (ctx.signals/rounds/sigma/klines), путей в плагинах нет; SOURCES — что плагин читает из ctx.
Ветвления по номеру Г нет: плагин выбирается по --mechanic. Закрытия — JSON portfolio-sim --closes-out: вариант → период → потолок →
[[мс, $], ...]. Выход — CSV «до/после» на (вариант, период, потолок, точка сетки). Точка вне домена плагина — отказ, не молчание.
  closure_mech.py --closes closes.json --mechanic g124 [--grid 'q=90,95;D=1,2'] --out out.csv   (--grid пуст = вся сетка домена)"""
import argparse, csv, glob, importlib, itertools, json, os, sys


KINDS = ("closes", "keep", "rounds")
SOURCES = ("signals", "rounds", "sigma", "klines", "regime")
S_V = "/data/p12r2/s-v"                     # off-дерево B1: <мес>/<сутки>/t-bid-btc4h-q1/{signals,rounds}.csv
STUDY = "/data/tk046/{mon}/study"           # свечи ref-<SYM>-1m.csv, sigma240/, regime/<сутки>.csv, approaches/D20/


def load_plugin(name):
    mod = importlib.import_module("closures." + name) if name.isidentifier() else None
    if mod is None or not all(hasattr(mod, a) for a in ("NAME", "PARAMS", "KIND", "SOURCES", "run")):
        raise SystemExit(f"плагин {name}: нужны NAME, PARAMS, KIND, SOURCES, run")
    if mod.KIND not in KINDS or not set(mod.SOURCES) <= set(SOURCES):
        raise SystemExit(f"плагин {name}: KIND из {KINDS}, SOURCES из {SOURCES}")
    return mod


class Ctx:
    """Ленивые загрузчики входов месяца; плагин берёт только объявленное в SOURCES (иначе отказ)."""

    def __init__(self, mon, allowed, s_v=S_V, study=None):
        self.mon, self.allowed, self.s_v = mon, set(allowed), s_v
        self.study = (study or STUDY).format(mon=mon)

    def _need(self, src):
        if src not in self.allowed:
            raise SystemExit(f"источник {src} не объявлен в SOURCES плагина")

    def _day_files(self, name, form):
        base = os.path.join(self.s_v, self.mon)
        for day in sorted(os.listdir(base)):
            for d in sorted(glob.glob(os.path.join(base, day, form, name))):
                yield day, d

    def _table(self, name, form):
        out = []
        for _, path in self._day_files(name, form):
            with open(path, encoding="utf-8", newline="") as f:
                out += list(csv.DictReader(l for l in f if not l.startswith("#")))
        return out

    def signals(self, form="t-bid-btc4h-q1"):
        self._need("signals"); return self._table("signals.csv", form)

    def rounds(self, form="t-bid-btc4h-q1"):
        self._need("rounds"); return self._table("rounds.csv", form)

    def sigma(self, sym):
        self._need("sigma")
        with open(os.path.join(self.study, "sigma240", f"sigma-{sym}.csv"), encoding="utf-8") as f:
            return [(int(r["window_end_ms"]), float(r["sigma_bps"])) for r in csv.DictReader(f)]

    def klines(self, sym):
        self._need("klines")
        with open(os.path.join(self.study, "klines", f"ref-{sym}-1m.csv"), encoding="utf-8") as f:
            return [(int(r["minute_ms"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in csv.DictReader(f)]

    def regime(self, day):
        self._need("regime")
        with open(os.path.join(self.study, "regime", f"{day}.csv"), encoding="utf-8") as f:
            return list(csv.DictReader(f))


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


def run_all(plugin, closes, spec="", ctx=None):
    if plugin.KIND != "closes":
        raise SystemExit(f"KIND {plugin.KIND}: цепочка busy-replay → expo → psim и гейт тождества с closes-b1d (calc) ещё не подключены")
    rows = []
    for variant, per in sorted(closes.items()):
        for period, caps in sorted(per.items()):
            for cap, base in sorted(caps.items()):
                base = [(int(m), float(u)) for m, u in (b[:2] for b in base)]
                n0, s0 = len(base), sum(u for _, u in base)
                for pt in grid_points(plugin.PARAMS, spec):
                    fills = plugin.run(base, ctx, **pt)
                    rows.append({"mechanic": plugin.NAME, "variant": variant, "period": period, "cap": cap,
                                 "point": ";".join(f"{k}={pt[k]}" for k in sorted(pt)), "n_before": n0,
                                 "sum_before": round(s0, 4), "n_after": len(fills),
                                 "sum_after": round(sum(u for _, u in fills), 4)})
    return rows


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--closes", required=True); ap.add_argument("--mechanic", required=True)
    ap.add_argument("--grid", default=""); ap.add_argument("--month", default="")  # ctx.month для KIND keep/rounds
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    plugin = load_plugin(a.mechanic)
    rows = run_all(plugin, json.load(open(a.closes, encoding="utf-8")), a.grid, Ctx(a.month, plugin.SOURCES))
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["mechanic"], lineterminator="\n")
        w.writeheader(); w.writerows(rows)
    print("строк", len(rows))


if __name__ == "__main__":
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    main()
