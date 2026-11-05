#!/usr/bin/env python3
"""TK-493 F4 (G3): сверка панелей .sap со свечами xdl независимо от Rust-конвертера — python 3, stdlib.

Правило свёртки (src/statarb/panel.rs): строка с объёмом ≠ 0 даёт open/close на своей минуте, строка с объёмом 0 и минута без
строки — NaN, панель идёт от первой до последней строки с объёмом ≠ 0. Сверка побитовая (verify_panels.verify, TK-487).
  sweep       все файлы свечей ферм <work>/src/<биржа> → <work>/verify/sweep.csv (панель есть: полная сверка; нет: счёт строк)
  gate        G3: по 3 символа на биржу (правило выбора ниже) — повторная сверка и таблица → <work>/verify/gate.csv
  crosscheck  три источника: sweep ↔ манифест ворот данных F1 (rows, v0 по символу) ↔ итоги Rust convert (summary_exchange.csv)

Правило выбора трёх символов (НАЗНАЧЕНО без Судьи, подтвердить), детерминированное, среди символов с панелью:
  major — крипта с базой BTC/XBT, котировка USDT, затем USD, затем прочие; внутри — больше строк csv, затем алфавит;
          нет такой — крипта с максимумом строк: полный ряд;
  max_nan — максимум NaN в панели (дыры и нулевой объём), затем алфавит: проверка NaN-политики;
  late — позже всех начатая панель (листинг в окне), затем алфавит: проверка смещения начала и конца спана."""
import argparse
import csv
import os
import sys
from multiprocessing import Pool

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))  # tools/compute/statarb
import panels_manifest as pm  # noqa: E402
import verify_panels as vp  # noqa: E402

EXCHANGES = ["binance", "bybit", "okx", "bingx", "bitget", "kraken", "aster", "lighter"]
SWEEP_COLS = ["exchange", "symbol", "csv_rows", "zero_volume", "panel", "panel_minutes", "panel_nan", "diffs", "first_diff"]


def raw_scan(path):
    """Строки данных и строки с объёмом 0 (как convert_csv_stats), без панели."""
    rows = zero = 0
    with vp.open_text(path) as f:
        for i, line in enumerate(f):
            parts = line.strip().split(",")
            if len(parts) < 5:
                continue
            try:
                int(parts[0])
            except ValueError:
                continue  # заголовок или мусор: convert на первой строке пропускает, на остальных падает — ловит sweep панели
            rows += 1
            try:
                if len(parts) > 5 and float(parts[5]) == 0.0:
                    zero += 1
            except ValueError:
                pass  # объём не число: Rust считает строку не нулевой (parse → None)
    return rows, zero


def sweep_one(job):
    ex, sym, csv_path, panel = job
    if os.path.exists(panel):
        s, diffs = vp.verify(sym, panel, csv_path)
        return [ex, sym, s["csv_rows"], s["zero_volume"], 1, s["panel_minutes"], s["panel_nan"], s["diffs"],
                diffs[0] if diffs else ""]
    rows, zero = raw_scan(csv_path)
    return [ex, sym, rows, zero, 0, 0, 0, 0, ""]


def cmd_sweep(a):
    jobs = []
    for ex in a.exchanges:
        d = os.path.join(a.work, "src", ex)
        for name in sorted(os.listdir(d)):
            if name.endswith(pm.CSV_GZ):
                sym = name[: -len(pm.CSV_GZ)]
                jobs.append((ex, sym, os.path.join(d, name), os.path.join(a.work, "panels", ex, sym + ".sap")))
    with Pool(a.procs) as p:
        res = sorted(p.imap_unordered(sweep_one, jobs, chunksize=4))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(SWEEP_COLS)
        w.writerows(res)
    bad = 0
    for ex in a.exchanges:
        r = [x for x in res if x[0] == ex]
        d = sum(x[7] for x in r)
        bad += d
        print("sweep %-8s файлов %4d, с панелью %4d, строк csv %9d, объём 0 → NaN %9d, минут в панелях %9d, NaN в панелях %9d, расхождений %d"
              % (ex, len(r), sum(x[4] for x in r), sum(x[2] for x in r), sum(x[3] for x in r), sum(x[5] for x in r),
                 sum(x[6] for x in r), d))
    print("sweep: файлов %d, расхождений %d → %s" % (len(res), bad, a.out))
    return 1 if bad else 0


def read_csv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f))


def pick3(ex, root, work, sweep):
    """→ [(роль, символ)] по правилу из шапки."""
    inst = {r["symbol"]: r for r in read_csv(os.path.join(root, ex, "instruments.csv"))}
    info = {}
    for r in sweep:
        if r["exchange"] == ex and r["panel"] == "1":
            sym = r["symbol"]
            first, _n = pm.read_header(os.path.join(work, "panels", ex, sym + ".sap"))
            i = inst.get(sym, {})
            info[sym] = dict(first=first, nan=int(r["panel_nan"]), rows=int(r["csv_rows"]), base=i.get("base", "").upper(),
                             cls=i.get("class", ""), q={"USDT": 0, "USD": 1}.get(i.get("quote", "").upper(), 2))
    picks = []
    majors = sorted((s for s, i in info.items() if i["cls"] == "crypto" and i["base"] in ("BTC", "XBT")),
                    key=lambda s: (info[s]["q"], -info[s]["rows"], s))
    if not majors:
        majors = sorted((s for s, i in info.items() if i["cls"] == "crypto"), key=lambda s: (-info[s]["rows"], s))
    if majors:
        picks.append(("major", majors[0]))
    used = {s for _r, s in picks}
    rest = sorted(s for s in info if s not in used)
    if rest:
        s = min(rest, key=lambda s: (-info[s]["nan"], s))
        picks.append(("max_nan", s))
        used.add(s)
    rest = sorted(s for s in info if s not in used)
    if rest:
        picks.append(("late", min(rest, key=lambda s: (-info[s]["first"], s))))
    return picks


def cmd_gate(a):
    sweep = read_csv(a.sweep)
    rows, bad = [], 0
    for ex in a.exchanges:
        for role, sym in pick3(ex, a.root, a.work, sweep):
            s, diffs = vp.verify(sym, os.path.join(a.work, "panels", ex, sym + ".sap"),
                                 os.path.join(a.work, "src", ex, sym + pm.CSV_GZ))
            bad += s["diffs"]
            rows.append([ex, role, sym, s["csv_rows"], s["zero_volume"], s["panel_minutes"], s["panel_nan"], s["diffs"],
                         diffs[0] if diffs else ""])
            print("gate %-8s %-8s %-22s строк csv %6d, объём 0 → NaN %6d, минут %6d, NaN %6d, расхождений %d"
                  % (ex, role, sym, s["csv_rows"], s["zero_volume"], s["panel_minutes"], s["panel_nan"], s["diffs"]))
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    with open(a.out, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["exchange", "role", "symbol", "csv_rows", "zero_volume", "panel_minutes", "panel_nan", "diffs", "first_diff"])
        w.writerows(rows)
    print("gate: символов %d, расхождений %d → %s" % (len(rows), bad, a.out))
    return 1 if bad else 0


def cmd_crosscheck(a):
    sweep = read_csv(a.sweep)
    gate = {(r["exchange"], r["symbol"]): r for r in read_csv(a.gate_manifest) if r["grid"] == "candles" and r["has_candles"] == "1"}
    out, mism = [], 0
    seen = set()
    for r in sweep:
        k = (r["exchange"], r["symbol"])
        seen.add(k)
        g = gate.get(k)
        if g is None:
            out.append([k[0], k[1], r["csv_rows"], r["zero_volume"], "", "", "нет в манифесте ворот"])
            mism += 1
            continue
        ok = int(g["rows"]) == int(r["csv_rows"]) and int(g["v0"]) == int(r["zero_volume"])
        mism += not ok
        if not ok:
            out.append([k[0], k[1], r["csv_rows"], r["zero_volume"], g["rows"], g["v0"], "расхождение"])
    missing = sorted(k for k in gate if k not in seen and k[0] in a.exchanges)
    for k in missing:
        out.append([k[0], k[1], "", "", gate[k]["rows"], gate[k]["v0"], "в манифесте ворот есть, в свёртке нет"])
    mism += len(missing)
    print("crosscheck sweep ↔ манифест ворот F1: символов sweep %d, манифест %d, расхождений rows/v0 %d, только в манифесте %d"
          % (len(sweep), sum(1 for k in gate if k[0] in a.exchanges), mism - len(missing), len(missing)))
    tot = 0
    if a.summary:
        for r in read_csv(a.summary):
            ex = r["exchange"]
            s = [x for x in sweep if x["exchange"] == ex]
            pairs = [("строк csv", r["rust_rows"], sum(int(x["csv_rows"]) for x in s)),
                     ("объём 0", r["rust_zero"], sum(int(x["zero_volume"]) for x in s)),
                     ("минут в панелях", r["rust_minutes"], sum(int(x["panel_minutes"]) for x in s)),
                     ("панелей", r["rust_panels"], sum(int(x["panel"]) for x in s))]
            for name, rust, py in pairs:
                tot += str(rust) != str(py)
                if str(rust) != str(py):
                    print("  ИТОГИ %s %s: Rust %s ≠ sweep %s" % (ex, name, rust, py))
        print("crosscheck итоги Rust convert ↔ sweep: расхождений %d" % tot)
    if out:
        with open(a.out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f, lineterminator="\n")
            w.writerow(["exchange", "symbol", "sweep_rows", "sweep_zero", "gate_rows", "gate_v0", "note"])
            w.writerows(out)
        print("список расхождений → %s" % a.out)
    return 1 if (mism or tot) else 0


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(q):
        q.add_argument("--exchanges", type=lambda s: s.split(","), default=EXCHANGES)

    s = sub.add_parser("sweep")
    s.add_argument("--work", required=True)
    s.add_argument("--out", required=True)
    s.add_argument("--procs", type=int, default=8)
    common(s)
    s.set_defaults(fn=cmd_sweep)
    g = sub.add_parser("gate")
    g.add_argument("--root", required=True)
    g.add_argument("--work", required=True)
    g.add_argument("--sweep", required=True)
    g.add_argument("--out", required=True)
    common(g)
    g.set_defaults(fn=cmd_gate)
    c = sub.add_parser("crosscheck")
    c.add_argument("--sweep", required=True)
    c.add_argument("--gate-manifest", required=True)
    c.add_argument("--summary")
    c.add_argument("--out", required=True)
    common(c)
    c.set_defaults(fn=cmd_crosscheck)
    a = p.parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
