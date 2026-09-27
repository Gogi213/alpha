#!/usr/bin/env python3
"""TK-010, П-07 поправка 5 (п. 1–4): чтение июля 2026 — ворота данных и счёт пяти форм.

Путь каждой формы — тот же, что в `p07-read.py`: busy-replay (июль, `epochs/e-jul/b5/<клетка>`, busy-skip off)
-> portfolio-sim (одна эпоха `июль`, $2500/$500, без потолка, без дневного стопа и BTC-kill, TRX вне пула)
-> KPI `kpi-newhigh.rolling_kpi(h = 5 сут)` -> `verdict_kpi_fixed` (правка Судьи 27.09). Функции берутся из
`p07-read.py` / `kpi-newhigh.py` / `p07-h9r-h14.py` / `p07-h9h10.py` через importlib без копирования.

Границы месяца: `rolling_kpi` задаёт их строками `kn.ms("2026-08-01")` (начало), `kn.ms("2026-09-24")` (конец
данных, цензура) и `kn.ms("2026-09-01")` (раздел месяцев). Здесь `kn.ms` подменяется только для этих трёх строк:
начало 2026-07-01, конец данных = раздел = 2026-08-01 00:00 UTC — сетка 744 часа июля, хвост до 01.08 (п. 1),
весь июль — месяц «aug» модуля, «sep» пуст. Остальная логика KPI не тронута.

Формы (колонка `form` rounds.csv, сверены по строке 3 rounds.csv на диске 28.09):
    main    b5/p07m-main    ladder3x2..20w2-pct2-tr1x1-14400-ttl1800        база сравнения (В-104)
    a-base  b5/p07a-base    pct2-tr1x1-14400-ttl1800                        база Г-85а
    b-base  b5/p07b-base    ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800   база Г-85б
    b-k1    b5/p07b-base + keep K<=1 (`p07-h9r-h14.simulate(..., "b", "k1")`, кэш `study/approaches/D20` июля)
    a-fr1   b5/p07a-h2-fr1  single@fr+1-pct2-tr1x1-14400-ttl1800            справочно (п. 4)

Ворота данных (п. 2, `--data-gate`): по суткам —
  * `.done` у всех четырёх каталогов `b5/<клетка>/<сутки>/`;
  * пул — символы `study/root-<сутки>/instruments.csv` без TRXUSDT (В-105); монета «с данными» — есть
    `<SYM>-<сутки>.binlog` и `verify-<SYM>.status` == ok (тот же критерий K1, без которого `bounce-grid` отказывает
    монете); стоп — нет данных у > 10 % монет пула;
  * `n_no_sigma` — строка `bounce-grid` «n_no_sigma N из M сигналов» в `tmp-p07/cells-by-day/jul-<сутки>.grid.log`
    (σ нужна только форме b-base; M — сигналы того же прохода); стоп — N > 1 % M.
Печать — только «сутки: ok / СТОП (флаги)» и итог, без долей и сумм.

Счёт (`--out`): ворота данных проверяются молча; не пройдены — отказ. В stdout — только путь к файлу и «готово»,
числа — только в json. Ворота метода 03.08 — `p07-jul-cells.py --gate` (отдельно, до этого скрипта).

    python3 ~/alpha/bin/p07-jul-read.py --data-gate
    python3 ~/alpha/bin/p07-jul-read.py --out ~/alpha/tmp-p07/jul-read/jul-read.json
"""
import argparse
import datetime as dt
import importlib.util
import json
import os
import re
import subprocess
import sys

HOME = os.path.expanduser("~/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))
JUL_HOME = os.path.join(HOME, "epochs/e-jul")
OUT_ROOT = os.path.join(HOME, "tmp-p07/jul-read")
GRID_LOGS = os.path.join(HOME, "tmp-p07/cells-by-day")
SET_ = "t-bid-btc4h-q1"
JUL_DAYS = [f"2026-07-{d:02d}" for d in range(1, 32)]
DROP = "TRXUSDT"
POOL_MISS_MAX = 0.10   # п. 2: нет данных у > 10 % монет пула — стоп
NO_SIGMA_MAX = 0.01    # п. 2: n_no_sigma > 1 % сигналов — стоп
MARGIN = 0.10          # п. 4: доля клетки <= доля базы − 0,10

# (форма, каталог b5, метка form в rounds.csv, keep-клетка p07-h9r-h14 или None)
FB = "ladder3x0..0.0409sw2-pct2-tr1x1-14400-ttl1800"
FORMS = [
    ("main", "p07m-main", "ladder3x2..20w2-pct2-tr1x1-14400-ttl1800", None),
    ("a-base", "p07a-base", "pct2-tr1x1-14400-ttl1800", None),
    ("b-base", "p07b-base", FB, None),
    ("b-k1", "p07b-base", FB, "k1"),
    ("a-fr1", "p07a-h2-fr1", "single@fr+1-pct2-tr1x1-14400-ttl1800", None),
]
CELL_DIRS = sorted({c for _, c, _, _ in FORMS})
PAIRS = [("b-k1", "b-base"), ("a-fr1", "a-base")]  # п. 4: справочная клетка против своей базы
NO_SIGMA_RE = re.compile(r"n_no_sigma (\d+) из (\d+) сигналов")


def load_mod(name, alias):
    """Сосед в той же папке (репозиторий) или `~/alpha/tmp-p07/` (Steam Deck)."""
    for d in (HERE, os.path.join(HOME, "tmp-p07")):
        p = os.path.join(d, name)
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location(alias, p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise SystemExit(f"нет {name} ни в {HERE}, ни в ~/alpha/tmp-p07")


# ---------- ворота данных ----------

def day_gate(day):
    """Флаги стопа суток (пустой список — ok). Числа наружу не отдаются."""
    flags = []
    for c in CELL_DIRS:
        if not os.path.exists(os.path.join(JUL_HOME, "b5", c, day, ".done")):
            flags.append(f"нет .done {c}")
    root = os.path.join(JUL_HOME, "study", f"root-{day}")
    inst = os.path.join(root, "instruments.csv")
    if not os.path.exists(inst):
        flags.append("нет instruments.csv")
    else:
        with open(inst, encoding="utf-8") as fh:
            pool = [l.split(",", 1)[0].strip() for l in fh.read().splitlines()[1:] if l.strip()]
        pool = [s for s in pool if s != DROP]
        miss = 0
        for s in pool:
            st = os.path.join(root, f"verify-{s}.status")
            ok = (os.path.exists(os.path.join(root, f"{s}-{day}.binlog")) and os.path.exists(st)
                  and open(st, encoding="utf-8").read().strip() == "ok")
            miss += not ok
        if not pool or miss > POOL_MISS_MAX * len(pool):
            flags.append("монеты пула без данных > 10 %")
    log = os.path.join(GRID_LOGS, f"jul-{day}.grid.log")
    m = None
    if os.path.exists(log):
        with open(log, encoding="utf-8", errors="replace") as fh:
            found = NO_SIGMA_RE.findall(fh.read())
        m = found[-1] if found else None
    if m is None:
        flags.append("нет строки n_no_sigma в grid-логе")
    else:
        n_no, n_sig = int(m[0]), int(m[1])
        if n_no > NO_SIGMA_MAX * n_sig:
            flags.append("n_no_sigma > 1 % сигналов")
    return flags


def data_gate(verbose):
    all_ok = True
    for day in JUL_DAYS:
        flags = day_gate(day)
        all_ok &= not flags
        if verbose:
            print(f"{day}: " + ("ok" if not flags else "СТОП (" + "; ".join(flags) + ")"))
    if verbose:
        print("ВОРОТА ДАННЫХ ИЮЛЯ:", "ok" if all_ok else "НЕ ПРОЙДЕНЫ")
    return all_ok


# ---------- счёт ----------

def patch_july(kn):
    """Границы месяца в kpi-newhigh — июль (см. докстринг): подмена только трёх строк-дат."""
    orig = kn.ms
    remap = {"2026-08-01": "2026-07-01", "2026-09-24": "2026-08-01", "2026-09-01": "2026-08-01"}
    kn.ms = lambda day: orig(remap.get(day, day))
    return orig


def portfolio_sim_jul(pr, d, form, out_dir):
    j, co = os.path.join(d, "ps.json"), os.path.join(d, "ps-closes.json")
    pr.run(["python3", pr.PORT_SIM,
            "--epoch", f"июль={out_dir}:.",
            "--variant", f"cell={SET_}/{form}",
            "--klines", os.path.join(JUL_HOME, "study/klines"),
            "--klines", os.path.join(HOME, "study/klines"),
            "--deposit-usd", "2500", "--position-usd", "500", "--max-pos", "0",
            "--day-stop-pct", "0", "--btc-kill-bps", "0", "--drop", DROP,
            "--json", j, "--closes-out", co])
    with open(co, encoding="utf-8") as fh:
        c = json.load(fh)
    return sorted(tuple(x) for x in c.get("cell", {}).get("июль", {}).get("0", []))


def run_form(pr, hr, ph, name, cell_dir, form, keep_cell):
    d = os.path.join(OUT_ROOT, name)
    out_dir = os.path.join(d, "jul")
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["python3", pr.BUSY_REPLAY, os.path.join(JUL_HOME, "b5", cell_dir), out_dir, "--sets", SET_]
    n_keep = None
    if keep_cell:
        keep = hr.simulate(ph, "b", keep_cell)
        n_keep = len(keep)
        kp = os.path.join(d, "keep.csv")
        ph.write_keep(keep, kp)
        cmd += ["--keep", kp]
    pr.run(cmd)
    closes = portfolio_sim_jul(pr, d, form, out_dir)
    return closes, pr.symbol_map({"jul": out_dir}, form), n_keep


def verdict_one_month(pr, kn, closes, sym):
    """`verdict_kpi_fixed` на одном месяце: месяц подаётся в обе ячейки aug/sep, берётся состояние aug."""
    roll = kn.rolling_kpi(closes, h_days=5)
    m = roll["aug"]
    point = m["frac_gt_h"]
    dayf = pr.excl_day_fracs(kn, closes)["aug"]
    symf = pr.excl_symbol_fracs(kn, closes, sym)
    symf = None if symf is None else symf["aug"]
    v, states, note, day_max, sym_max = pr.verdict_kpi_fixed(
        {"aug": point, "sep": point}, {"aug": dayf, "sep": dayf},
        None if symf is None else {"aug": symf, "sep": symf})
    state = {"OK": "проходит", "FAIL": "не проходит", "EDGE": "на границе"}.get(states["aug"], states["aug"])
    pool = dayf + (symf or [])
    mx = m["max"]
    return {
        "frac_gt_5d": point, "n_main": m["n_main"], "h_hours": m["h_hours"],
        "excl_max": max(pool) if pool else None, "excl_min": min(pool) if pool else None,
        "stability_day_max": day_max["aug"], "stability_symbol_max": sym_max["aug"],
        "state": state, "note": note,
        "max_wait_hours": mx["max"], "max_wait_censored": mx["max_censored"],
        "max_wait_text": (("≥ " if mx["max_censored"] else "") + f"{mx['max']:.1f} ч") if mx["max"] is not None else None,
    }


def paired_b2(kn, orig_ms, cell, base):
    """Б2 парно: для каждых суток d июля — доля клетки без d <= доля базы без d − 0,10."""
    rows = []
    for day in JUL_DAYS:
        d0 = orig_ms(day)
        d1 = d0 + 86_400_000
        fc = kn._frac_gt_h_pair([x for x in cell if not (d0 <= x[0] < d1)], 5)[0]
        fb = kn._frac_gt_h_pair([x for x in base if not (d0 <= x[0] < d1)], 5)[0]
        ok = fc is not None and fb is not None and fc <= round(fb - MARGIN, 3) + 1e-9
        rows.append({"day": day, "cell": fc, "base": fb, "ok": ok})
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--data-gate", action="store_true", help="ворота данных п. 2: только статусы по суткам")
    g.add_argument("--out", help="json отчёта (числа — только туда), например ~/alpha/tmp-p07/jul-read/jul-read.json")
    a = ap.parse_args()
    if a.data_gate:
        sys.exit(0 if data_gate(verbose=True) else 1)
    if not data_gate(verbose=False):
        print("ворота данных июля не пройдены — счёт не начат (см. --data-gate)")
        sys.exit(1)

    pr = load_mod("p07-read.py", "p07read")
    ph = load_mod("p07-h9h10.py", "p07base")
    hr = load_mod("p07-h9r-h14.py", "p07h9r")
    ph.HOMES = {"jul": JUL_HOME}  # keep K<=1 строится по дому июля, кэш подходов study/approaches/D20
    kn = pr.load_mod(pr.KN_PATH, "kn")
    orig_ms = patch_july(kn)
    os.makedirs(OUT_ROOT, exist_ok=True)

    res = {"_meta": {"protocol": "П-07 поправка 5", "days": [JUL_DAYS[0], JUL_DAYS[-1]], "h_days": 5,
                     "hours_grid": "2026-07-01..2026-08-01 00:00 UTC (744 ч)", "deposit": 2500, "position": 500,
                     "max_pos": 0, "drop": DROP, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()}}
    closes_by = {}
    for name, cell_dir, form, keep_cell in FORMS:
        closes, sym, n_keep = run_form(pr, hr, ph, name, cell_dir, form, keep_cell)
        closes_by[name] = closes
        units, oor = pr.daily_series(closes, JUL_DAYS)
        row = {"form": form, "cell_dir": cell_dir, "keep": keep_cell, "n_signals_kept": n_keep,
               "n_trades": len(closes), "n_out_of_month": oor, "n_trades_note": pr.n_note(len(closes)),
               "usd": pr.block_boot(units)}
        row["kpi"] = verdict_one_month(pr, kn, closes, sym) if closes else None
        res[name] = row

    for cell, base in PAIRS:
        c, b = res[cell], res[base]
        uc, _ = pr.daily_series(closes_by[cell], JUL_DAYS)
        ub, _ = pr.daily_series(closes_by[base], JUL_DAYS)
        diff = pr.block_boot([x - y for x, y in zip(uc, ub)])
        fc = (c["kpi"] or {}).get("frac_gt_5d")
        fb = (b["kpi"] or {}).get("frac_gt_5d")
        b1 = fc is not None and fb is not None and fc <= round(fb - MARGIN, 3) + 1e-9
        b2rows = paired_b2(kn, orig_ms, closes_by[cell], closes_by[base])
        b2 = all(r["ok"] for r in b2rows)
        b3 = diff["ci95"][1] >= 0
        c["vs_base"] = {"base": base, "B1": b1, "B2": b2, "B2_by_day": b2rows, "B3": b3, "diff_usd": diff,
                        "result": "кандидат" if (b1 and b2 and b3) else "справочная"}

    with open(a.out, "w", encoding="utf-8", newline="") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(a.out)
    print("готово")


if __name__ == "__main__":
    main()
