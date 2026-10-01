#!/usr/bin/env python3
"""П-10: ворота (б), (в), (г) §9 п. 3 и счёт клеток H–S/Г-89: keep → busy-replay → portfolio-sim (TK-024).

Путь тот же, что у П-08 (`p08-run.py`): `busy_replay_for` / `portfolio_sim_for` — импортом из `p07-h9h10.py` (одно
определение), меняется только каталог выхода (`tmp-p10/run`); для клетки Г-89 на время вызова подменяется дерево
(`p07b-t9-market`) и форма круга. keep-файлы — из `p10-cov.py keep` (`tmp-p10/cov/keep/keep-<клетка>.csv`).

Режимы:
  gate   — (б) `p10-keepall` / `-b2` байт в байт с `tmp-p08/run/h9-b-p08-keepall[-b2]/ps-closes.json`; `p10-keepall-market`
           = закрытия `p07b-t9-market` после busy-replay; (в) признаки H, I, J, K, L, O на 03.08 независимым кодом,
           доля определённых; (г) список K1 `p10-days.csv` (symbol,day,status). Итог вливается в
           `tmp-p10/cov/p10-coverage.json` (ключ `gates`); печатается только статус. `--parts b,v,g` — часть ворот.
  cells  — клетки из `--cells` (после заморозки охвата): `tmp-p10/run/h9-b-<клетка>/ps-closes.json` (потолок 0)
           и `h9-b-<клетка>-b2/` (B2, потолок 3). Без `--cells` — все итоговые клетки JSON.

На деке: python3 bin/p10-run.py gate
"""
import argparse
import csv
import importlib.util
import json
import math
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HOME = os.path.expanduser("~/alpha")
COV = f"{HOME}/tmp-p10/cov"
RUN = f"{HOME}/tmp-p10/run"
MKT_FORM = "market-pct2-tr1x1-14400-ttl1800"
GATE_DAY = "2026-08-03"
GATE_REFS = (("p10-keepall", 0, "tmp-p08/run/h9-b-p08-keepall/ps-closes.json"),
             ("p10-keepall-b2", 3, "tmp-p08/run/h9-b-p08-keepall-b2/ps-closes.json"))


def load_mod(fname, alias):
    for d in (HERE, os.path.join(HOME, "bin"), os.path.join(HOME, "tmp-p07")):
        p = os.path.join(d, fname)
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location(alias, p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    sys.exit(f"нет {fname} рядом, в ~/alpha/bin и ~/alpha/tmp-p07")


class MarketTree:
    """На время вызова `p07base` читает дерево/форму клетки market вместо B1 (Г-89)."""

    def __init__(self, p, active):
        self.p, self.active = p, active

    def __enter__(self):
        if self.active:
            self.old = (self.p.base_dir_for, self.p.FORM_OF["b"])
            self.p.base_dir_for = lambda _v: "p07b-t9-market"
            self.p.FORM_OF["b"] = MKT_FORM

    def __exit__(self, *a):
        if self.active:
            self.p.base_dir_for, self.p.FORM_OF["b"] = self.old


def read_keep3(path):
    with open(path, encoding="utf-8", newline="") as fh:
        return [(r["symbol"], r["t0_ns"], r["price_tick"]) for r in csv.DictReader(fh)]


def run_chain(p, name, rows, max_pos, market=False):
    """keep → busy-replay → portfolio-sim; → путь ps-closes.json и число оставленных сигналов."""
    keep_path = os.path.join(p.OUT_ROOT, f"h9-b-{name}", "keep.csv")
    p.write_keep(sorted(set(rows)), keep_path)
    with MarketTree(p, market):
        outs = p.busy_replay_for("b", name, keep_path)
        p.portfolio_sim_for("b", name, outs, max_pos=max_pos)
    return os.path.join(p.OUT_ROOT, f"h9-b-{name}", "ps-closes.json"), len(set(rows)), outs


def same_file(a, b):
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def n_closes(path):
    j = json.load(open(path, encoding="utf-8"))
    return sum(len(v) for var in j.values() for per in var.values() for v in per.values())


def same_content(got, ref, cap):
    """Фолбэк: те же закрытия по ключу потолка (имена путей внутри не сравниваются)."""
    g, r = json.load(open(got, encoding="utf-8")), json.load(open(ref, encoding="utf-8"))
    return all(g.get(v, {}).get(pk, {}).get(str(cap)) == r.get(v, {}).get(pk, {}).get(str(cap))
               and r.get(v, {}).get(pk, {}).get(str(cap)) is not None for v in g for pk in ("август", "сентябрь"))


# ---------- (б) ----------

def key_sets_equal(p, fm):
    """Сигналы `p08-b1` и `p07b-base` (цепочка `p07base` читает вторую) — одни и те же ключи на авг 01–31 + сен 01–23."""
    bad = 0
    for m in ("aug", "sep"):
        for home, days in fm.month_homes(m):
            for d in days:
                ks = []
                for tree in ("p08-b1", "p07b-base"):
                    sp = os.path.join(home, "b5", tree, d, fm.SET_, "signals.csv")
                    with open(sp, encoding="utf-8", newline="") as fh:
                        ks.append({(r["symbol"], r["t0_ns"], r["price_tick"], r["form"], r["signal_index"])
                                   for r in csv.DictReader(line for line in fh if not line.startswith("#"))})
                bad += ks[0] != ks[1]
    return bad


def gate_b(p, fm):
    res, ok = {}, True
    bad = key_sets_equal(p, fm)
    res["p08_b1_eq_p07b_base_days_differ"] = bad
    ok &= bad == 0
    rows = read_keep3(f"{COV}/keep/keep-p10-keepall.csv")
    for name, cap, ref in GATE_REFS:
        got, n, _ = run_chain(p, name, rows, cap)
        ref = os.path.join(HOME, ref)
        same = same_file(got, ref) or same_content(got, ref, cap)
        res[name] = {"signals": n, "closes": n_closes(got), "ref": ref, "byte_equal": same_file(got, ref), "ok": same}
        print(f"ворота (б) {name}: сигналов {n}, против {ref}: {'ЗЕЛЁНОЕ' if same else 'КРАСНОЕ'}")
        ok &= same
    mrows = read_keep3(f"{COV}/keep/keep-p10-keepall-market.csv")
    got, n, _ = run_chain(p, "p10-keepall-market", mrows, 0, market=True)
    with MarketTree(p, True):
        outs = p.busy_replay_for("b", "p10-market-ref", None)
        p.portfolio_sim_for("b", "p10-market-ref", outs, max_pos=0)
    ref = os.path.join(p.OUT_ROOT, "h9-b-p10-market-ref", "ps-closes.json")
    same = same_file(got, ref) or same_content(got, ref, 0)
    nc = n_closes(got)
    res["p10-keepall-market"] = {"signals": n, "closes": nc, "ref": "busy-replay без фильтра на p07b-t9-market",
                                 "byte_equal": same_file(got, ref), "ok": same,
                                 "note": "принятых кругов нет" if nc == 0 else ""}
    print(f"ворота (б) p10-keepall-market: сигналов {n}, закрытий {nc}, против busy-replay без фильтра: "
          f"{'ЗЕЛЁНОЕ' if same else 'КРАСНОЕ'}")
    ok &= same
    return res, ok


# ---------- (в): независимый пересчёт признаков на 03.08 (свой разбор CSV и свои циклы, без Bars/f_*) ----------

def i_load(paths):
    d = {}
    for pth in paths:
        if not os.path.exists(pth):
            continue
        with open(pth, encoding="utf-8") as fh:
            rd = csv.reader(fh)
            ix = {n: i for i, n in enumerate(next(rd))}
            for row in rd:
                d[int(row[ix["minute_ms"]])] = (float(row[ix["high"]]), float(row[ix["low"]]), float(row[ix["close"]]),
                                                float(row[ix["volume"]] or 0))
    return d


def i_close(d, t):
    for back in range(6):  # последняя закрытая цена не старше 5 минут
        v = d.get(t - back * 60_000)
        if v:
            return v[2]
    return None


def i_bars(d, m, k):
    """k подряд минут, оканчивающихся на m; любая отсутствует — None."""
    out = [d.get(m - j * 60_000) for j in range(k - 1, -1, -1)]
    return None if any(x is None for x in out) else out


def indep_day(fm, day=GATE_DAY):
    """{(symbol, signal_index): {H,I,J,K,L,O}} по signals.csv суток из e-aug, всё считается заново."""
    home = f"{HOME}/epochs/e-aug"
    sp = f"{home}/b5/p08-b1/{day}/t-bid-btc4h-q1/signals.csv"
    with open(sp, encoding="utf-8", newline="") as fh:
        sigs = [r for r in csv.DictReader(line for line in fh if not line.startswith("#")) if r["symbol"] != "TRXUSDT"]
    with open(f"{home}/study/root-{day}/instruments.csv", encoding="utf-8", newline="") as fh:
        ins = {r["symbol"]: float(r["tick_size"]) for r in csv.DictReader(fh)}
    kd = [f"{h}/study/klines" for h in fm.ALL_HOMES]
    btc = i_load([f"{h}/study/regime/ref-BTCUSDT-1m.csv" for h in fm.ALL_HOMES])
    out = {}
    for sym in sorted({r["symbol"] for r in sigs}):
        c = i_load([os.path.join(d, f"ref-{sym}-1m.csv") for d in kd])
        sg = {}
        with open(f"{HOME}/study/sigma240/sigma-{sym}.csv", encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                sg[int(row["window_end_ms"])] = float(row["sigma_bps"])
        app = {}
        with open(f"{home}/study/approaches-t28/D20/{day}/approaches-{sym}.csv", encoding="utf-8", newline="") as fh:
            for row in csv.DictReader(fh):
                if row["side"] == "bid":
                    app[(int(row["arm_ms"]), int(row["price_tick"]))] = int(row["best_own_tick"])
        for r in (x for x in sigs if x["symbol"] == sym):
            m = int(r["t0_ns"]) // 10 ** 9 // 60 * 60_000 - 60_000
            res = dict.fromkeys(("H", "I", "J", "K", "L", "O"))
            a, b, ba, bb = i_close(c, m - 3_600_000), i_close(c, m), i_close(btc, m - 3_600_000), i_close(btc, m)
            if a and b and ba and bb:
                res["H"] = abs((b / a - 1) * 1e4 - (bb / ba - 1) * 1e4)
            w = i_bars(c, m, 61)
            if w:
                path = 0.0
                for j in range(1, 61):
                    path += abs(w[j][2] - w[j - 1][2])
                res["I"] = abs(w[60][2] - w[0][2]) / path if path > 0 else None
            best = app.get((int(r["t0_ns"]) // 10 ** 6, int(r["price_tick"])))
            apx = best * ins[sym] if best is not None else None
            w5 = i_bars(c, m, 5)
            if w5 and apx:
                res["J"] = (max(x[0] for x in w5) - apx) / apx * 1e4
            w60 = i_bars(c, m, 60)
            sig = sg.get(m + 60_000)
            if w60 and apx and sig:
                res["K"] = (apx - min(x[1] for x in w60)) / apx * 1e4 / sig
            w24 = i_bars(c, m, 1440)
            if w24:
                res["L"] = sum(x[3] * x[2] for x in w24)
            px = float(r["entry_px"])
            if w60 and px:
                hi, lo = max(x[0] for x in w60), min(x[1] for x in w60)
                res["O"] = abs((px - lo) / (hi - lo) - 0.5) if hi > lo else None
            out[(sym, r["signal_index"])] = res
    return out


GATE_V = {"H": "coin_abs_minus_btc_1h", "I": "g62_er", "J": "g74_hi5_bps", "K": "g76_low60_sigma",
          "L": "g22_turnover24_usd", "O": "g31_off_center"}


def gate_v(fm):
    ind = indep_day(fm)
    rows = [r for r in fm.read_feats(f"{COV}/aug/feats-p10.csv") if r["day_utc"] == GATE_DAY]
    mine = {(r["symbol"], r["signal_index"]): r for r in rows}
    res, ok = {"day": GATE_DAY, "signals": len(rows), "signals_indep": len(ind), "features": {}}, True
    if set(mine) != set(ind):
        res["key_mismatch"] = len(set(mine) ^ set(ind))
        ok = False
    for k, col in GATE_V.items():
        bad = n_def = 0
        for key, r in mine.items():
            a, b = r[col], ind.get(key, {}).get(k)
            n_def += a is not None
            same = (a is None and b is None) or (a is not None and b is not None and math.isclose(a, b, rel_tol=1e-9, abs_tol=1e-12))
            bad += not same
        res["features"][col] = {"defined": n_def, "defined_share": round(n_def / len(rows), 4) if rows else 0.0,
                                "mismatches": bad}
        ok &= bad == 0
        print(f"ворота (в) {col}: на {GATE_DAY} определено {n_def}/{len(rows)}, расхождений {bad}")
    print(f"ворота (в): {'ЗЕЛЁНОЕ' if ok and rows else 'КРАСНОЕ'}")
    return res, bool(ok and rows)


# ---------- (г): список K1 ----------

def gate_g(fm, out_csv):
    ref = fm.Ref()
    pairs = set()
    for m in ("aug", "sep"):
        pairs |= {(r["symbol"], r["day_utc"]) for r in fm.read_feats(f"{COV}/{m}/feats-p10.csv")}
    counts, lines = {}, []
    for sym, day in sorted(pairs):
        root = ref.day_root(day)
        st = "нет данных"
        if root:
            pth = os.path.join(root, f"verify-{sym}.status")
            if os.path.exists(pth):
                st = open(pth, encoding="utf-8").read().strip() or "нет данных"
        counts[st] = counts.get(st, 0) + 1
        lines.append((sym, day, st))
    os.makedirs(os.path.dirname(os.path.abspath(out_csv)), exist_ok=True)
    with open(out_csv, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(["symbol", "day", "status"])
        w.writerows(lines)
    print(f"ворота (г): пар (монета, сутки) {len(lines)}, статусы {counts} → {out_csv}")
    return {"pairs": len(lines), "status_counts": counts, "file": out_csv}, True


def merge_gates(res):
    path = f"{COV}/p10-coverage.json"
    j = json.load(open(path, encoding="utf-8"))
    j.setdefault("gates", {}).update(res)
    with open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(j, fh, ensure_ascii=False, indent=1)


def cmd_cells(p, names):
    j = json.load(open(f"{COV}/p10-coverage.json", encoding="utf-8"))
    allc = [c["name"] for cl in j["final"].values() for c in cl]
    for cell in names or allc:
        rows = read_keep3(f"{COV}/keep/keep-{cell}.csv")
        market = cell == "p10-g89-mkt-big"
        path, n, outs = run_chain(p, cell, rows, 0, market=market)
        with MarketTree(p, market):
            p.portfolio_sim_for("b", f"{cell}-b2", outs, max_pos=3)
        print(f"{cell}: оставлено сигналов {n} → {path}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("cmd", choices=["gate", "cells"])
    ap.add_argument("--parts", default="b,v,g")
    ap.add_argument("--cells", default="")
    ap.add_argument("--days-out", default=f"{COV}/p10-days.csv")
    a = ap.parse_args()
    p = load_mod("p07-h9h10.py", "p07base")
    p.OUT_ROOT = RUN
    if a.cmd == "cells":
        cmd_cells(p, [c for c in a.cells.split(",") if c])
        return
    fm = load_mod("p10-feats.py", "p10f")
    res, ok = {}, True
    for part in a.parts.split(","):
        if part == "b":
            r, o = gate_b(p, fm)
        elif part == "v":
            r, o = gate_v(fm)
        elif part == "g":
            r, o = gate_g(fm, a.days_out)
        else:
            sys.exit(f"неизвестная часть ворот {part}")
        res[part] = r
        res[part]["ok"] = o
        ok &= o
    merge_gates({**res, "ok": ok})
    print("ИТОГ ворот:", "ЗЕЛЁНОЕ" if ok else "КРАСНОЕ — стоп")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
