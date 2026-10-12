#!/usr/bin/env python3
"""В-227 пакет F (TK-200): раннер Python-механик по закрытиям B1 (М5). Без канонического хеша, вне хеш-счёта Летописи (П-класс П-14 §3.2).
Плагин tools/registry/closures/<имя>.py: NAME, PARAMS {параметр: кортеж допустимых значений}, KIND, SOURCES, run(data, ctx, **params)
(решение Судьи 12.10, design-v227 §2.4): KIND='closes' — data = закрытия B1, выход [(мс, $)]; 'keep' — data = строки signals.csv + исход
rounds.csv, выход = набор ключей keep (цепочка busy-replay → expo → psim — на calc); 'rounds' — data = rounds.csv, выход {ключ: (exit_ns, exit_px)}.
ctx — ленивые загрузчики по месяцу (ctx.signals/rounds/sigma/klines), путей в плагинах нет; SOURCES — что плагин читает из ctx.
Ветвления по номеру Г нет: плагин выбирается по --mechanic. Закрытия — JSON portfolio-sim --closes-out: вариант → период → потолок →
[[мс, $], ...]. Выход — CSV «до/после» на (вариант, период, потолок, точка сетки). Точка вне домена плагина — отказ, не молчание.
  closure_mech.py --closes closes.json --mechanic g124 [--grid 'q=90,95;D=1,2'] --out out.csv   (--grid пуст = вся сетка домена)"""
import argparse, csv, glob, importlib, itertools, json, os, subprocess, sys


KINDS = ("closes", "keep", "rounds")
SOURCES = ("signals", "rounds", "sigma", "klines", "regime", "approaches")
S_V = "/data/p12r2/s-v"                     # off-дерево B1: <мес>/<сутки>/t-bid-btc4h-q1/{signals,rounds}.csv
ABBR = ("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec")   # каталоги /data/tk046/<abbr>
STUDY = "/data/tk046/{abbr}/study"                                   # klines/ref-<SYM>-1m.csv, approaches/D20/<сутки>/approaches-<SYM>.csv
EPOCH_STUDY = "/data/tk046/{abbr}/home/alpha/epochs/e-{abbr}/study"  # sigma240/sigma-<SYM>.csv, regime/<сутки>.csv (путь sigma_from из шапки rounds)


def load_plugin(name):
    mod = importlib.import_module("closures." + name) if name.isidentifier() else None
    if mod is None or not all(hasattr(mod, a) for a in ("NAME", "PARAMS", "KIND", "SOURCES", "run")):
        raise SystemExit(f"плагин {name}: нужны NAME, PARAMS, KIND, SOURCES, run")
    if mod.KIND not in KINDS or not set(mod.SOURCES) <= set(SOURCES):
        raise SystemExit(f"плагин {name}: KIND из {KINDS}, SOURCES из {SOURCES}")
    return mod


class Ctx:
    """Ленивые загрузчики входов месяца; плагин берёт только объявленное в SOURCES (иначе отказ)."""

    def __init__(self, mon, allowed, s_v=S_V, study=None, epoch_study=None):
        self.mon, self.allowed, self.s_v = mon, set(allowed), s_v
        abbr = ABBR[int(mon[5:7]) - 1] if mon else ""
        self.study = (study or STUDY).format(abbr=abbr)
        self.epoch_study = (epoch_study or EPOCH_STUDY).format(abbr=abbr)

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
        with open(os.path.join(self.epoch_study, "sigma240", f"sigma-{sym}.csv"), encoding="utf-8") as f:
            return [(int(r["window_end_ms"]), float(r["sigma_bps"])) for r in csv.DictReader(f)]

    def klines(self, sym):
        self._need("klines")
        with open(os.path.join(self.study, "klines", f"ref-{sym}-1m.csv"), encoding="utf-8") as f:
            return [(int(r["minute_ms"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])) for r in csv.DictReader(f)]

    def approaches(self, sym, day):
        """Подходы D20 монеты за сутки: {(arm_ms, price_tick): строка} только side=bid (связь с сигналом B1 — p07-h9r-h14.py)."""
        self._need("approaches")
        path = os.path.join(self.study, "approaches", "D20", day, f"approaches-{sym}.csv")
        if not os.path.exists(path):
            return {}
        with open(path, encoding="utf-8") as f:
            return {(int(r["arm_ms"]), r["price_tick"]): r for r in csv.DictReader(f) if r["side"] == "bid"}

    def regime(self, day):
        self._need("regime")
        with open(os.path.join(self.epoch_study, "regime", f"{day}.csv"), encoding="utf-8") as f:
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


B1_FORM = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
CHAIN = os.path.join(os.path.dirname(os.path.abspath(__file__)), "m5_chain.sh")
GATE_REF = "/data/p12r3a/closes-b1-cap{cap}-{mon}.json"   # эталон TK-191 (cap0 2750 сд./+52,32 $); closes-b1d пуст ({}) - vn-b без базы B1


def write_keep(path, keys):
    """keys — (symbol, day_utc, form, signal_index): формат --keep busy-replay.py (вид symbol,day_utc,signal_index,form)."""
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, lineterminator="\n"); w.writerow(["symbol", "day_utc", "signal_index", "form"])
        for sym, day, form, idx in sorted(keys):
            w.writerow([sym, day, idx, form])


def chain(mon, work, keep_path=None):
    """Цепочка M5 на calc (bash m5_chain.sh); возвращает {cap: путь closes}. Сбой — отказ, не молчание."""
    r = subprocess.run(["bash", CHAIN, mon, work] + ([keep_path] if keep_path else []))
    if r.returncode or not os.path.exists(os.path.join(work, "chain.done")):
        raise SystemExit(f"цепочка М5 {mon}: rc={r.returncode}, см. {work}/fail.txt, busy.txt")
    return {c: os.path.join(work, f"closes-m5-cap{c}-{mon}.json") for c in (0, 3)}


def gate(mon, work):
    """Гейт «выкл = прежний»: keep=все (без --keep) -> закрытия байт в байт = closes-b1. Расхождение — отказ."""
    got, bad = chain(mon, work), []
    for cap, path in got.items():
        ref = GATE_REF.format(cap=cap, mon=mon)
        if open(path, "rb").read() != open(ref, "rb").read():
            bad.append(f"cap{cap}: {path} != {ref}")
    if bad:
        raise SystemExit("гейт М5 НЕ пройден: " + "; ".join(bad))
    print(f"гейт М5 {mon}: тождество с closes-b1 cap0/cap3 — байт в байт")


def _stats(path):
    """(сделок, $) закрытий одного прогона psim: вариант → период → потолок → [[мс, $]]."""
    n, s = 0, 0.0
    for per in json.load(open(path, encoding="utf-8")).values():
        for caps in per.values():
            for rows in caps.values():
                n += len(rows); s += sum(float(r[1]) for r in rows)
    return n, round(s, 4)


def keep_data(ctx):
    """Сигналы B1 месяца с исходом круга (net_bps, reason) по ключу (symbol, day_utc, form, signal_index); круга нет — поля пустые."""
    out = {(r["symbol"], r["day_utc"], r["form"], r["signal_index"]): r for r in ctx.rounds() if r["form"] == B1_FORM}
    data = []
    for s in ctx.signals():
        if s["form"] != B1_FORM:
            continue
        k = (s["symbol"], s["day_utc"], s["form"], s["signal_index"])
        data.append({**s, "net_bps": out[k]["net_bps"] if k in out else "", "reason": out[k]["reason"] if k in out else ""})
    return data


def run_keep(plugin, mon, work, spec="", chain_fn=None):
    """KIND='keep': плагин → набор ключей keep → busy-replay → psim; строки «до/после» по потолку (до = цепочка без фильтра)."""
    chain_fn = chain_fn or chain
    ctx = Ctx(mon, tuple(plugin.SOURCES) + ("signals", "rounds"))
    data = keep_data(ctx)
    base = {c: _stats(p) for c, p in chain_fn(mon, os.path.join(work, "base")).items()}
    rows = []
    for i, pt in enumerate(grid_points(plugin.PARAMS, spec)):
        keys = {(d["symbol"], d["day_utc"], d["form"], d["signal_index"]) for d in data}
        kept = set(plugin.run(data, ctx, **pt))
        if not kept <= keys:
            raise SystemExit(f"{plugin.NAME} {pt}: плагин вернул ключи вне сигналов B1 месяца ({len(kept - keys)})")
        if not kept:   # пустой keep busy-replay не принимает; результат известен без цепочки
            rows += [{"mechanic": plugin.NAME, "month": mon, "cap": c, "point": ";".join(f"{k}={pt[k]}" for k in sorted(pt)),
                      "signals_before": len(keys), "signals_kept": 0, "n_before": base[c][0], "sum_before": base[c][1],
                      "n_after": 0, "sum_after": 0.0} for c in sorted(base)]
            continue
        w = os.path.join(work, f"pt{i}")
        os.makedirs(w, exist_ok=True); write_keep(os.path.join(w, "keep.csv"), kept)
        for c, p in chain_fn(mon, w, os.path.join(w, "keep.csv")).items():
            n, s = _stats(p)
            rows.append({"mechanic": plugin.NAME, "month": mon, "cap": c, "point": ";".join(f"{k}={pt[k]}" for k in sorted(pt)),
                         "signals_before": len(keys), "signals_kept": len(kept), "n_before": base[c][0], "sum_before": base[c][1],
                         "n_after": n, "sum_after": s})
    return rows


def run_all(plugin, closes, spec="", ctx=None):
    if plugin.KIND != "closes":
        raise SystemExit(f"KIND {plugin.KIND}: через run_keep (--work), KIND rounds ещё не подключён")
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
    ap.add_argument("--gate", metavar="РАБ", help="гейт тождества на calc: --month M --gate <рабочий каталог>, остальное не нужно")
    ap.add_argument("--closes"); ap.add_argument("--mechanic")
    ap.add_argument("--grid", default=""); ap.add_argument("--month", default="")  # ctx.month для KIND keep/rounds
    ap.add_argument("--out"); ap.add_argument("--work", help="рабочий каталог цепочки (KIND keep, calc)")
    a = ap.parse_args(argv)
    if a.gate:
        return gate(a.month, a.gate)
    if not (a.mechanic and a.out and (a.closes or a.work)):
        ap.error("нужны --mechanic, --out и --closes (KIND closes) или --month и --work (KIND keep), либо --gate")
    plugin = load_plugin(a.mechanic)
    if plugin.KIND == "keep":
        rows = run_keep(plugin, a.month, a.work, a.grid)
    else:
        rows = run_all(plugin, json.load(open(a.closes, encoding="utf-8")), a.grid, Ctx(a.month, plugin.SOURCES))
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]) if rows else ["mechanic"], lineterminator="\n")
        w.writeheader(); w.writerows(rows)
    print("строк", len(rows))


if __name__ == "__main__":
    sys.path.insert(0, __import__("os").path.dirname(__import__("os").path.abspath(__file__)))
    main()
