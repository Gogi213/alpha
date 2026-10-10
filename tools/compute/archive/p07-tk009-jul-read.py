#!/usr/bin/env python3
"""TK-009, июль по В-146 (Судья `6960d7d`): чтение 33 различных клеток пачки на июле 2026 — П-07 поправка 5 п. 4 без
изменений, парно к базе Г-85б K≤1 по суткам июля: С1 доля ≤ доля K≤1 − 0,10; С2 то же без любых одних суток (парно);
С3 верх 95 % Δ$ ≥ 0. Все три — кандидат на январь–июнь (июль использован для выбора); иначе описание. Холм по Δ$ —
m = 33 (h2-0.0273 ≡ h2-0.0136 после K≤1 в авг/сен — читается, но в m одна; если на июле разные — пометка).

Путь каждой клетки — тот же, что у `b-k1` в `p07-jul-read.py` (функции оттуда и из `p07-read.py`/`p07-h9h10.py`/
`p07-h9r-h14.py` без копирования): keep K≤1 (`p07-h9r-h14.simulate`, каталог и форма клетки подменяются, как
`--base-dir/--form` в авг/сен) → busy-replay (`epochs/e-jul/b5/<клетка>`) → portfolio-sim (июль, $2500/$500, потолок —
0, у k1cap5 — 5) → `rolling_kpi(h = 5 сут)` → `verdict_kpi_fixed`. k1f25 — порог H1 f25 (2,4666, П-07) по доле фронтрана
из кэша подходов июля (`p07-h1-join.py` с домом июля → `j9-read/h1-join-jul.json`; соединение не 100 % — «не определена»).

Ворота до чтения: клетки заданий `p07-tk009-jul.py` — `--gate` там (03.08 ok) и `.done` у всех 32 каталогов × 31 сутки;
ворота данных июля (пул, n_no_sigma) — `p07-jul-read.data_gate` (те же сутки и сигналы). Печать — только статусы ворот
и путь к json (числа — в json).

    python3 p07-tk009-jul-read.py --out ~/alpha/tmp-p07/j9-read/j9-read.json      # в ~/alpha/tmp-p07 (соседи)
    python3 p07-tk009-jul-read.py --out … --only b-k1                             # тождество базы с TK-010
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import sys

HOME = os.path.expanduser("~/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))


def load(fname, alias):
    for d in (HERE, os.path.join(HOME, "tmp-p07"), os.path.join(HOME, "bin")):
        p = os.path.join(d, fname)
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location(alias, p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise FileNotFoundError(fname)


JR = load("p07-jul-read.py", "p07julread")
JC = load("p07-tk009-jul.py", "p07tk009jul")
pc = JC.pc
TR = load("p07-tk009-read.py", "p07tk009read")  # boot_p — тот же, что в чтении авг/сен
OUT_ROOT = os.path.join(HOME, "tmp-p07/j9-read")
FB = JR.FB
M_HOLM = 33


def cell_rows():
    """(имя, каталог b5, форма, набор-подпапка, keep-клетка, потолок)."""
    out = [("b-k1", "p07b-base", FB, pc.BASE_SET, "k1", 0)]
    for c in JC.OLD_CELLS:
        out.append((c[0], c[0], pc.label(c), c[8], "k1", 0))
    for name, label, _sig in JC.NEW_APPR + JC.NEW_TOUCH:
        out.append((name, name, label, pc.BASE_SET, "k1", 0))
    out.append(("k1f25", "p07b-base", FB, pc.BASE_SET, "k1f25", 0))
    out.append(("k1cap5", "p07b-base", FB, pc.BASE_SET, "k1cap5", 5))
    return out


def cells_gate():
    bad = []
    for name, cell_dir, *_ in cell_rows():
        miss = [d for d in JR.JUL_DAYS if not os.path.exists(f"{JR.JUL_HOME}/b5/{cell_dir}/{d}/.done")]
        if miss:
            bad.append(f"{cell_dir}: нет {len(miss)} суток")
    return bad


def portfolio_sim(pr, d, set_dir, form, out_dir, cap):
    j, co = os.path.join(d, "ps.json"), os.path.join(d, "ps-closes.json")
    pr.run(["python3", pr.PORT_SIM,
            "--epoch", f"июль={out_dir}:.",
            "--variant", f"cell={set_dir}/{form}",
            "--klines", os.path.join(JR.JUL_HOME, "study/klines"),
            "--klines", os.path.join(HOME, "study/klines"),
            "--deposit-usd", "2500", "--position-usd", "500", "--max-pos", str(cap),
            "--day-stop-pct", "0", "--btc-kill-bps", "0", "--drop", JR.DROP,
            "--json", j, "--closes-out", co])
    with open(co, encoding="utf-8") as fh:
        c = json.load(fh)
    return sorted(tuple(x) for x in c.get("cell", {}).get("июль", {}).get(str(cap), []))


def run_cell(pr, hr, ph, name, cell_dir, form, set_dir, keep_cell, cap):
    d = os.path.join(OUT_ROOT, name)
    out_dir = os.path.join(d, "jul")
    os.makedirs(out_dir, exist_ok=True)
    ph.base_dir_for = lambda _v: cell_dir
    ph.FORM_OF["b"] = form
    ph.SET_ = set_dir
    keep = hr.simulate(ph, "b", keep_cell)
    kp = os.path.join(d, "keep.csv")
    ph.write_keep(keep, kp)
    pr.run(["python3", pr.BUSY_REPLAY, os.path.join(JR.JUL_HOME, "b5", cell_dir), out_dir, "--sets", set_dir,
            "--keep", kp])
    closes = portfolio_sim(pr, d, set_dir, form, out_dir, cap)
    return closes, pr.symbol_map({"jul": out_dir}, form), len(keep)


def h1_join_july():
    """Доля фронтрана к сигналам `p07b-base` июля — `p07-h1-join.py` с домом июля; файл — в j9-read."""
    hj = load("p07-h1-join.py", "p07h1join")
    hj.HOMES = {"jul": JR.JUL_HOME}
    path = os.path.join(OUT_ROOT, "h1-join-jul.json")
    argv = sys.argv
    sys.argv = ["p07-h1-join.py", "--cell", "p07b-base", "--out", path]
    try:
        hj.main()
    finally:
        sys.argv = argv
    return path


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--only", help="через запятую — только эти клетки (проверка)")
    a = ap.parse_args()
    only = set(a.only.split(",")) if a.only else None
    rows = [r for r in cell_rows() if only is None or r[0] in only or r[0] == "b-k1"]
    bad = cells_gate() if only is None else []
    if bad or not JR.data_gate(verbose=False):
        print("ворота не пройдены — счёт не начат:", "; ".join(bad) or "данные июля (p07-jul-read --data-gate)")
        sys.exit(1)

    pr = JR.load_mod("p07-read.py", "p07read")
    ph = JR.load_mod("p07-h9h10.py", "p07base")
    hr = JR.load_mod("p07-h9r-h14.py", "p07h9r")
    ph.HOMES = {"jul": JR.JUL_HOME}
    kn = pr.load_mod(pr.KN_PATH, "kn")
    orig_ms = JR.patch_july(kn)
    os.makedirs(OUT_ROOT, exist_ok=True)

    if any(r[4] == "k1f25" for r in rows):
        jp = h1_join_july()

        def load_jul():
            d = json.load(open(jp, encoding="utf-8"))
            assert d["unmatched"] == 0 and d["ambiguous"] == 0, "H1: соединение с кэшем июля не 100 %"
            return {(r[1], r[2], r[3]): r[7] for r in d["rows"]}
        hr.load_h1_share = load_jul

    res = {"_meta": {"protocol": "П-07 поправка 5 п. 4; отбор В-146 (TK-009)", "m_holm": M_HOLM,
                     "days": [JR.JUL_DAYS[0], JR.JUL_DAYS[-1]], "h_days": 5, "deposit": 2500, "position": 500,
                     "drop": JR.DROP, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()}}
    closes_by = {}
    for name, cell_dir, form, set_dir, keep_cell, cap in rows:
        try:
            closes, sym, n_keep = run_cell(pr, hr, ph, name, cell_dir, form, set_dir, keep_cell, cap)
        except AssertionError as e:
            res[name] = {"cell_dir": cell_dir, "form": form, "undefined": str(e)}
            continue
        closes_by[name] = closes
        units, oor = pr.daily_series(closes, JR.JUL_DAYS)
        res[name] = {"cell_dir": cell_dir, "form": form, "set": set_dir, "keep": keep_cell, "max_pos": cap,
                     "n_signals_kept": n_keep, "n_trades": len(closes), "n_out_of_month": oor,
                     "usd": pr.block_boot(units),
                     "kpi": JR.verdict_one_month(pr, kn, closes, sym) if closes else None}

    base = closes_by["b-k1"]
    fb = (res["b-k1"]["kpi"] or {}).get("frac_gt_5d")
    ub, _ = pr.daily_series(base, JR.JUL_DAYS)
    for name, *_ in rows:
        if name == "b-k1" or name not in closes_by:
            continue
        c = res[name]
        uc, _ = pr.daily_series(closes_by[name], JR.JUL_DAYS)
        diff = pr.block_boot([x - y for x, y in zip(uc, ub)])
        fc = (c["kpi"] or {}).get("frac_gt_5d")
        c1 = fc is not None and fb is not None and fc <= round(fb - JR.MARGIN, 3) + 1e-9
        c2rows = JR.paired_b2(kn, orig_ms, closes_by[name], base)
        c2 = all(r["ok"] for r in c2rows)
        c3 = diff["ci95"][1] >= 0
        c["p"] = round(TR.boot_p([x - y for x, y in zip(uc, ub)]), 5)
        c["vs_k1"] = {"C1": c1, "C2": c2, "C2_by_day": c2rows, "C3": c3, "diff_usd": diff,
                      "result": "кандидат на янв–июн" if (c1 and c2 and c3) else "описание"}
    # Холм по Δ$ к K≤1: m = 33 (h2-0.0273 ≡ h2-0.0136 в авг/сен — в семью не входит)
    fam = sorted((res[n]["p"], n) for n in closes_by if "p" in res[n] and n != "p07b-h2-0.0273")
    stop = False
    for i, (pv, n) in enumerate(fam):
        ok = (not stop) and pv <= 0.05 / (M_HOLM - i)
        stop = stop or not ok
        res[n]["holm_sig"] = ok
    if "p07b-h2-0.0136" in closes_by and "p07b-h2-0.0273" in closes_by:
        res["_meta"]["h2_0136_eq_0273"] = closes_by["p07b-h2-0.0136"] == closes_by["p07b-h2-0.0273"]

    with open(a.out, "w", encoding="utf-8", newline="") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(a.out)
    print("готово")


if __name__ == "__main__":
    main()
