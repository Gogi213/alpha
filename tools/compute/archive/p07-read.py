#!/usr/bin/env python3
"""П-07 ступень 1-2 (Г-85а): чтение базы + H6 + H7 + H2 (В-124 родословная).

Путь каждой клетки — тот же, что у H9/H10 (`p07-h9h10.py`): busy-replay (T-31, engine-занятость по
(symbol,day,form)) -> portfolio-sim (счёт депозита, $2500/$500, без потолка) -> KPI (`kpi-newhigh.py`
rolling_kpi/stability_by_day/stability_by_symbol/verdict_kpi, П-07 §6/§10, H=5 суток). Клетки ступени 3
(H3-H5, H8) НЕ читаются здесь (правило Судьи, запечатаны). H9/H10 уже посчитаны отдельно
(`tmp-p07/h9-a-*/ps-closes.json`, `data/p07/h9h10-a.json`) — здесь только достраиваются интервал
Δ$ и устойчивость для них, без нового busy-replay/portfolio-sim (переиспользуются существующие
busy-replay каталоги h9-a-pause0 для символьной карты pause*/cap*).

Форма (колонка `form` в rounds.csv, `--variant <имя>=<набор>/<форма>` portfolio-sim) — считана с диска
заранее (переменные ниже), не угадана: single@fr — без префикса у base/H6/H7, `single@fr+N-` у H2.

    python3 p07-read.py --out tmp-p07/read-a.json
"""
import argparse
import csv
import datetime as dt
import glob
import importlib.util
import json
import math
import os
import random
import subprocess
import sys

HOME = os.path.expanduser("~/alpha")
SET_ = "t-bid-btc4h-q1"
BUSY_REPLAY = os.path.join(HOME, "bin/busy-replay.py")
PORT_SIM = os.path.join(HOME, "tmp-kpi/portfolio-sim.py")
KN_PATH = os.path.join(HOME, "tmp-t32/kpi-newhigh.py")
OUT_ROOT = os.path.join(HOME, "tmp-p07")
HOMES = {
    "aug": os.path.join(HOME, "epochs/e-aug"),
    "hist": os.path.join(HOME, "tmp-lsk0914.used-20260926/home"),
    "rec": os.path.join(HOME, "tmp-t29/rec"),
}

# (клетка, out_name на диске, form-метка rounds.csv) — форма снята с диска 27.09, см. докстринг
CELLS = [
    ("base", "p07a-base", "pct2-tr1x1-14400-ttl1800"),
    ("H6-pct1.5", "p07a-h6-pct1.5", "pct1.5-tr1x1-14400-ttl1800"),
    ("H6-pct3", "p07a-h6-pct3", "pct3-tr1x1-14400-ttl1800"),
    ("H6-before", "p07a-h6-before", "before-tr1x1-14400-ttl1800"),
    ("H7-tr1.5x1", "p07a-h7-tr1.5x1", "pct2-tr1.5x1-14400-ttl1800"),
    ("H7-tr1x1.5", "p07a-h7-tr1x1.5", "pct2-tr1x1.5-14400-ttl1800"),
    ("H7-tr2x1", "p07a-h7-tr2x1", "pct2-tr2x1-14400-ttl1800"),
    ("H7-1to1", "p07a-h7-1to1", "pct2-1to1-14400-ttl1800"),
    ("H2-fr1", "p07a-h2-fr1", "single@fr+1-pct2-tr1x1-14400-ttl1800"),
    ("H2-fr2", "p07a-h2-fr2", "single@fr+2-pct2-tr1x1-14400-ttl1800"),
    ("H2-fr3", "p07a-h2-fr3", "single@fr+3-pct2-tr1x1-14400-ttl1800"),
]

# H9/H10 — уже посчитаны (p07-h9h10.py); здесь читаются их готовые ps-closes.json без нового счёта.
# busy_dir_of: откуда брать rounds.csv для символьной карты (cap3/cap5 используют busy-replay пары pause0,
# как и сам p07-h9h10.py: portfolio_sim_for(..., outs0, max_pos=cap)).
# TK-004 (`--variant`): «b» — Г-85б целиком (база σ-лестница, одномерные оси, В-131/П-07 поправка 4),
# «a3» — ступень 3 и остаток Г-85а (запечатана: читать только по правилу А/Б/В). Клетки — из `p07-cells.py`.
EB = "ladder3x0..0.0409sw2"
EXITS = ["gone50wall0", "gone50wallx0", "gone50wallk0", "gone90wall0", "gone90wallx0", "gone90wallk0",
         "gone50wall10", "gone50wallx10", "gone50wallk10", "gone30wall0", "gone30wallx0", "gone30wallk0"]
H5 = [("btc1h", "t-bid-btc1h-q1"), ("btc2h", "t-bid-btc2h-q1"), ("btc3h", "t-bid-btc3h-q1")]


def axes_cells(v, entry, stops):
    pre = "" if entry == "single@fr" else f"{entry}-"
    f = lambda stop="pct2", take="tr1x1", dl=14400, ex=None:         f"{pre}{stop}-{take}-{dl}-ttl1800" + (f"-{ex}" if ex else "")
    c = [(f"H6-{s}", f"p07{v}-h6-{s}", f(stop=s), SET_) for s in stops]
    c += [(f"H13-{e}", f"p07{v}-h13-{e}", f(ex=e), SET_) for e in EXITS]
    c += [(f"H8-{d}", f"p07{v}-h8-{d}", f(dl=d), SET_) for d in (3600, 7200)]
    c += [(f"H4-{a}", f"p07{v}-h4-{a}", f(), SET_) for a in (900, 1800, 3600, 5400)]
    c += [(f"H5-{n}", f"p07{v}-h5-{n}", f(), s) for n, s in H5]
    c += [(f"H3-{u}", f"p07{v}-h3-{u}", f(), SET_) for u in (25000, 50000, 100000)]
    return c, f


def cells_for(variant):
    if variant == "a":
        return [(n, o, fm, SET_) for n, o, fm in CELLS]
    if variant == "a3":
        c, f = axes_cells("a", "single@fr", ["at", "behind"])
        return [("base", "p07a-base", f(), SET_)] + c
    c, f = axes_cells("b", EB, ["pct1.5", "pct3", "at", "behind"])
    h2 = [(f"H2-{k}", f"p07b-h2-{k}", f"ladder3x0..{k}sw2-pct2-tr1x1-14400-ttl1800", SET_)
          for k in ("0.0136", "0.0273", "0.0682")]
    h7 = [(f"H7-{t}", f"p07b-h7-{t}", f(take=t), SET_) for t in ("tr1.5x1", "tr1x1.5", "tr2x1", "1to1")]
    return [("base", "p07b-base", f(), SET_)] + h2 + c + h7


H9H10 = [
    ("H9-pause0(=base)", "pause0", "pause0", "0"),
    ("H9-pause30", "pause30", "pause30", "0"),
    ("H9-pause60", "pause60", "pause60", "0"),
    ("H9-pause120", "pause120", "pause120", "0"),
    ("H9-pauseStop60", "pauseStop60", "pauseStop60", "0"),
    ("H9-pauseStop120", "pauseStop120", "pauseStop120", "0"),
    ("H10-cap3", "cap3", "pause0", "3"),
    ("H10-cap5", "cap5", "pause0", "5"),
]
H9H10_FORM = "pct2-tr1x1-14400-ttl1800"

# Г-85б (TK-004): H10 (`p07-h9h10.py --variant b --no-pauses`) и H9р/H14/H1 (`p07-h9r-h14.py --variant b`) —
# готовые ps-closes.json в tmp-p07/h9-b-<тег>/; символьная карта — busy-replay того же каталога (cap — pause0).
REPLAY_B = [("H10-cap3", "cap3", "pause0", "3"), ("H10-cap5", "cap5", "pause0", "5")] + [
    (f"{ax}-{c}", f"h9r-{c}", f"h9r-{c}", "0") for ax, cells in
    (("H9r", ("s1", "s2", "s3")), ("H14", ("k1", "k2", "k3", "n2", "n3", "n4")), ("H1", ("f25", "f50", "f75")),
     ("keep", ("keep",))) for c in cells]

AUG_DAYS = [f"2026-08-{d:02d}" for d in range(1, 32)]
SEP_DAYS = [f"2026-09-{d:02d}" for d in range(1, 24)]  # 23 суток (В-115)


def run(cmd, **kw):
    r = subprocess.run(cmd, capture_output=True, text=True, **kw)
    if r.returncode != 0:
        print("CMD FAILED:", " ".join(cmd), file=sys.stderr)
        print(r.stdout[-3000:], file=sys.stderr)
        print(r.stderr[-3000:], file=sys.stderr)
        raise SystemExit(1)
    return r.stdout


def load_mod(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def busy_replay_cell(out_name, tag_name, set_=SET_, pre="read-a"):
    outs = {}
    for tag, home in HOMES.items():
        src = os.path.join(home, "b5", out_name)
        out_dir = os.path.join(OUT_ROOT, f"{pre}-{tag_name}", tag)
        os.makedirs(out_dir, exist_ok=True)
        run(["python3", BUSY_REPLAY, src, out_dir, "--sets", set_])
        outs[tag] = out_dir
    return outs


def portfolio_sim_cell(tag_name, form, outs, set_=SET_, pre="read-a"):
    d = os.path.join(OUT_ROOT, f"{pre}-{tag_name}")
    os.makedirs(d, exist_ok=True)
    j = os.path.join(d, "ps.json")
    co = os.path.join(d, "ps-closes.json")
    cmd = ["python3", PORT_SIM,
           "--epoch", f"история={outs['hist']}:.",
           "--epoch", f"запись={outs['rec']}:.",
           "--epoch", f"август={outs['aug']}:.",
           "--join", "сентябрь=история+запись",
           "--variant", f"cell={set_}/{form}",
           "--klines", os.path.join(HOME, "study/klines"),
           "--klines", os.path.join(HOME, "epochs/e-aug/study/klines"),
           "--deposit-usd", "2500", "--position-usd", "500", "--max-pos", "0",
           "--day-stop-pct", "0", "--btc-kill-bps", "0", "--drop", "TRXUSDT",
           "--json", j, "--closes-out", co]
    run(cmd)
    return json.load(open(j, encoding="utf-8", newline="")), json.load(open(co, encoding="utf-8", newline=""))


def closes_of(co, vname="cell", cap="0"):
    out = {}
    for pk, ru in (("aug", "август"), ("sep", "сентябрь")):
        out[pk] = [tuple(x) for x in co.get(vname, {}).get(ru, {}).get(cap, [])]
    return out


def symbol_map(dirs_by_tag, form, set_=SET_):
    """t1_ms (round(exit_ns/1e6)) -> symbol, из rounds.csv busy-replay выходов данной клетки/формы."""
    m = {}
    for tag, out_dir in dirs_by_tag.items():
        for f in glob.glob(os.path.join(out_dir, "20*", set_, "rounds.csv")):
            with open(f, encoding="utf-8", newline="") as fh:
                for r in csv.DictReader(l for l in fh if not l.startswith("#")):
                    if r["form"] != form:
                        continue
                    t1 = int(r["exit_ns"]) // 1_000_000  # как в portfolio-sim.py: x["t1"] // 1_000_000
                    m[t1] = r["symbol"]
    return m


def daily_series(closes_list, days):
    agg = {d: 0.0 for d in days}
    oor = 0
    for t, p in closes_list:
        d = dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).strftime("%Y-%m-%d")
        if d in agg:
            agg[d] += p
        else:
            oor += 1
    return [agg[d] for d in days], oor


def block_boot(units, reps=20000, seed=20260927):
    n = len(units)
    if n == 0:
        return {"n_days": 0, "b": None, "est": 0.0, "ci95": [0.0, 0.0]}
    b = max(1, math.ceil(n ** (1 / 3)))
    rng = random.Random(seed)
    k = math.ceil(n / b)
    out = []
    for _ in range(reps):
        s = []
        for _ in range(k):
            i = rng.randrange(n)
            s.extend(units[(i + j) % n] for j in range(b))
        out.append(sum(s[:n]))
    out.sort()
    lo, hi = out[int(0.025 * len(out))], out[int(0.975 * len(out)) - 1]
    return {"n_days": n, "b": b, "est": round(sum(units), 2), "ci95": [round(lo, 2), round(hi, 2)]}


def diff_vs_base(cell_closes, base_closes):
    """Парная Δ$ по суткам (день закрытия UTC, как в t32-boot.py), блочный бутстреп b=ceil(n^(1/3))."""
    res = {}
    for pk, days in (("aug", AUG_DAYS), ("sep", SEP_DAYS)):
        u_cell, _ = daily_series(cell_closes[pk], days)
        u_base, _ = daily_series(base_closes[pk], days)
        units = [c - b for c, b in zip(u_cell, u_base)]
        res[pk] = block_boot(units)
    return res


PASS_FRAC = 0.10


def excl_day_fracs(kn, closes, h_days=5):
    """{aug,sep}: доля > H при исключении КАЖДЫХ одних суток по очереди (список, не только худший)."""
    ev = sorted(closes)
    out = {"aug": [], "sep": []}
    if not ev:
        return out
    days = sorted({dt.datetime.fromtimestamp(t / 1000, dt.timezone.utc).date() for t, _ in ev})
    for day in days:
        d0 = int(dt.datetime.combine(day, dt.time(), tzinfo=dt.timezone.utc).timestamp() * 1000)
        d1 = d0 + 86_400_000
        rest = [(t, p) for t, p in ev if not (d0 <= t < d1)]
        fa, fs = kn._frac_gt_h_pair(rest, h_days)
        if fa is not None:
            out["aug"].append(fa)
        if fs is not None:
            out["sep"].append(fs)
    return out


def excl_symbol_fracs(kn, closes, symbol_of, h_days=5):
    """{aug,sep} при исключении КАЖДОЙ монеты по очереди, или None — нет полной карты символов."""
    ev = sorted(closes)
    if not ev or any(t not in symbol_of for t, _ in ev):
        return None
    syms = {symbol_of[t] for t, _ in ev}
    out = {"aug": [], "sep": []}
    for sym in syms:
        rest = [(t, p) for t, p in ev if symbol_of[t] != sym]
        fa, fs = kn._frac_gt_h_pair(rest, h_days)
        if fa is not None:
            out["aug"].append(fa)
        if fs is not None:
            out["sep"].append(fs)
    return out


def verdict_kpi_fixed(point, day_fracs, sym_fracs):
    """Правило Судьи (правка 27.09, после чтения kn.verdict_kpi): «проходит» — точка <= 0,10 И максимум по
    всем исключениям (любые одни сутки, любая монета) тоже <= 0,10 в обоих месяцах. «Не проходит» — точка >
    0,10 хотя бы в одном месяце, И минимум по всем исключениям этого месяца тоже > 0,10 (ни одно исключение
    не опускает до порога). Иначе — «на границе». kn.verdict_kpi (сравнивает FAIL с максимумом, не минимумом)
    здесь НЕ используется — это была ошибка в исходном модуле."""
    states, day_max, sym_max = {}, {}, {}
    for pk in ("aug", "sep"):
        b = point[pk]
        dpool = day_fracs.get(pk) or []
        spool = (sym_fracs.get(pk) if sym_fracs is not None else []) or []
        day_max[pk] = max(dpool) if dpool else None
        sym_max[pk] = max(spool) if (sym_fracs is not None and spool) else None
        if b is None:
            states[pk] = "нет данных"
            continue
        pool = dpool + (spool if sym_fracs is not None else [])
        mx = max(pool) if pool else b
        mn = min(pool) if pool else b
        if b <= PASS_FRAC and mx <= PASS_FRAC:
            states[pk] = "OK"
        elif b > PASS_FRAC and mn > PASS_FRAC:
            states[pk] = "FAIL"
        else:
            states[pk] = "EDGE"
    if "нет данных" in states.values():
        v = "нет данных"
    elif states["aug"] == "OK" and states["sep"] == "OK":
        v = "проходит"
    elif "FAIL" in states.values():
        v = "не проходит"
    else:
        v = "на границе"
    sym_checked = sym_fracs is not None
    note = ("суждение по точке на двух месяцах, не статистика (правило Судьи 27.09: «не проходит» — "
            "минимум по исключениям тоже > 0,10, не максимум)")
    if not sym_checked:
        note += "; устойчивость по монете не проверена — нет карты символов у этого ряда"
    return v, states, note, day_max, sym_max


def kpi_block(kn, all_closes, symbol_of):
    roll = kn.rolling_kpi(all_closes, h_days=5)
    point = {pk: roll[pk]["frac_gt_h"] for pk in ("aug", "sep")}
    n_main = {pk: roll[pk]["n_main"] for pk in ("aug", "sep")}
    day_fracs = excl_day_fracs(kn, all_closes)
    sym_fracs = excl_symbol_fracs(kn, all_closes, symbol_of)
    v, states, note, day_max, sym_max = verdict_kpi_fixed(point, day_fracs, sym_fracs)
    return {"frac_gt_5d": point, "n_main": n_main, "stability_day_max": day_max,
            "stability_symbol_max": sym_max, "states": states, "verdict": v, "note": note}


def n_note(n_trades):
    return "сделок нет" if n_trades < 10 else ("описание" if n_trades < 30 else "точка")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--variant", choices=["a", "a3", "b"], default="a",
                    help="a — ступени 1–2 Г-85а (прежнее), a3 — ступень 3/остаток Г-85а, b — Г-85б (TK-004)")
    ap.add_argument("--skip-compute", action="store_true",
                     help="не гонять busy-replay/portfolio-sim заново, только дочитать уже посчитанные read-a-*/ps-closes.json")
    a = ap.parse_args()
    kn = load_mod(KN_PATH, "kn")

    results = {}
    base_closes = None
    base_sym = None

    pre = "read-b" if a.variant == "b" else "read-a"
    for name, out_name, form, set_ in cells_for(a.variant):
        tag_name = name.lower().replace(".", "")
        d = os.path.join(OUT_ROOT, f"{pre}-{tag_name}")
        co_path = os.path.join(d, "ps-closes.json")
        if a.skip_compute and os.path.exists(co_path):
            co = json.load(open(co_path, encoding="utf-8", newline=""))
            outs = {tag: os.path.join(d, tag) for tag in HOMES}
        else:
            outs = busy_replay_cell(out_name, tag_name, set_, pre)
            _, co = portfolio_sim_cell(tag_name, form, outs, set_, pre)
        closes = closes_of(co)
        sym = symbol_map(outs, form, set_)
        all_closes = sorted(closes["aug"] + closes["sep"])
        n_aug, usd_aug = len(closes["aug"]), round(sum(p for _, p in closes["aug"]), 2)
        n_sep, usd_sep = len(closes["sep"]), round(sum(p for _, p in closes["sep"]), 2)
        row = {"form": form, "n": {"aug": n_aug, "sep": n_sep}, "usd": {"aug": usd_aug, "sep": usd_sep},
               "n_trades_note": {"aug": n_note(n_aug), "sep": n_note(n_sep)}}
        if n_aug >= 10 or n_sep >= 10:
            row["kpi"] = kpi_block(kn, all_closes, sym)
        else:
            row["kpi"] = None
        if name == "base":
            base_closes, base_sym = closes, sym
        else:
            row["diff_vs_base"] = diff_vs_base(closes, base_closes) if base_closes else None
        results[name] = row
        print(name, "n=", row["n"], "usd=", row["usd"],
              "frac=", (row["kpi"] or {}).get("frac_gt_5d"), flush=True)

    # H9/H10 — читаем готовые ps-closes.json (p07-h9h10.py), символьная карта — из соответствующих
    # busy-replay каталогов tmp-p07/h9-a-<busy_dir>/ (cap3/cap5 переиспользуют h9-a-pause0)
    replay = {"a": H9H10, "b": REPLAY_B}.get(a.variant, [])
    v = a.variant
    for name, out_tag, busy_dir, cap in replay:
        d = os.path.join(OUT_ROOT, f"h9-{v}-{out_tag}")
        co = json.load(open(os.path.join(d, "ps-closes.json"), encoding="utf-8", newline=""))
        closes = closes_of(co, vname=f"п07{v}", cap=cap)
        busy_dirs = {tag: os.path.join(OUT_ROOT, f"h9-{v}-{busy_dir}", tag) for tag in HOMES}
        sym = symbol_map(busy_dirs, H9H10_FORM if v == "a" else f"{EB}-pct2-tr1x1-14400-ttl1800")
        all_closes = sorted(closes["aug"] + closes["sep"])
        n_aug, usd_aug = len(closes["aug"]), round(sum(p for _, p in closes["aug"]), 2)
        n_sep, usd_sep = len(closes["sep"]), round(sum(p for _, p in closes["sep"]), 2)
        row = {"form": H9H10_FORM if v == "a" else f"{EB}-pct2-tr1x1-14400-ttl1800", "n": {"aug": n_aug, "sep": n_sep}, "usd": {"aug": usd_aug, "sep": usd_sep},
               "n_trades_note": {"aug": n_note(n_aug), "sep": n_note(n_sep)}}
        row["kpi"] = kpi_block(kn, all_closes, sym) if (n_aug >= 10 or n_sep >= 10) else None
        row["diff_vs_base"] = diff_vs_base(closes, base_closes) if base_closes else None
        results[name] = row
        print(name, "n=", row["n"], "usd=", row["usd"],
              "frac=", (row["kpi"] or {}).get("frac_gt_5d"), flush=True)

    with open(a.out, "w", encoding="utf-8", newline="") as f:
        json.dump(results, f, ensure_ascii=False, indent=1)
    print("DONE", a.out)


if __name__ == "__main__":
    main()
