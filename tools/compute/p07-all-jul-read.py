#!/usr/bin/env python3
"""TK-018: чтение июля 2026 для ВСЕХ вариантов дашборда (клетки `p07-all-jul.py`, дека + VPS).

Путь каждого варианта — тот же, что у его чтения авг/сен и у июля TK-010/TK-009 (функции `p07-jul-read.py`,
`p07-tk009-jul-read.py`, `p07-read.py`, `p07-h9h10.py`, `p07-h9r-h14.py` через importlib, без копирования):
keep-фильтр (если есть) → busy-replay (`epochs/e-jul/b5/<клетка>`, busy-skip off) → portfolio-sim (одна эпоха
«июль», $2500/$500, потолок — как у варианта, без дневного стопа и BTC-kill, TRX/KORU вне пула) →
`rolling_kpi(h = 5 сут)` → `verdict_one_month` (доля, наиб. ожидание, стабильность).

Список вариантов — от прогонов авг/сен (имя каталога = ключ, как в `dash-p07-all-dump.py`):
  read-{a,b}-<ось>         клетка П-07 (каталог июля ищется по форме и набору прогона авг/сен);
  h9-{a,b}-pause0 / cap3|5 база, потолок portfolio-sim;
  h9-{a,b}-pause30… / pauseStop…  `p07-h9h10.simulate_pause`;
  h9-{a,b}-h9r-<клетка>    `p07-h9r-h14.simulate` (s1–s3, k1–k3, n2–n4, f25–f75, keep);
  h9-b-h9r-p07b-base-k1*   K≤1 и его H1/H10-надстройки; h9-b-h9r-<каталог>-k1 — K≤1 на клетке пачки TK-009;
  плюс главный и соседи (`m-*`), П-02 (`p02-g105`, `p02-g86`, `p02-g08` — фильтр сделок главного, как
  `p02-variant-filter.py filter`), П-05 (`p05-<набор>`, 153).
П-08 — своё чтение TK-013 (другой бинарник), здесь нет.

Ворота до чтения: 03.08 (`p07-all-jul.py --gate` → `tmp-p07/jall-gate.status` = ok) и VPS (`jall-vgate.txt` ok),
`.done` у всех каталогов × 31 сутки, данные июля — `p07-jul-read.data_gate`. Печать — только статусы и путь.
Отбор (В-146, TK-018): «проверка на июле» — только отобранные ранее (TK-009 34 клетки — их чтение j9-read);
остальные — «описание, не отобран»; метка ставится при вливании в страницу, не здесь.

    python3 ~/alpha/tmp-p07/p07-all-jul-read.py --out ~/alpha/tmp-p07/jall-read/jall-read.json [--jobs N; 0 = по памяти] [--only a,b]
"""
import argparse
import csv
import datetime as dt
import glob
import importlib.util
import json
import multiprocessing as mp
import os
import shutil
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
    raise SystemExit(f"нет {fname}")


JR = load("p07-jul-read.py", "p07julread")
J9R = load("p07-tk009-jul-read.py", "p07tk009julread")
JA = load("p07-all-jul.py", "p07alljul")
PV = load("p02-variant-filter.py", "p02vf")
pc = JA.pc
JUL = JR.JUL_HOME
OUT_ROOT = os.path.join(HOME, "tmp-p07/jall-read")
MAIN_FORM = pc.label(JA.MAIN_EXTRA[0][0])


# ---------- список вариантов ----------
def grid_index():
    """(префикс стороны, форма, набор-подпапка) → каталог клетки июля."""
    idx = {}
    for c, _ in JA.grid_cells():
        idx.setdefault((c[0][:5], pc.label(c), c[8]), []).append(c[0])
    return idx


def rows():
    """[(имя, каталог b5 июля, форма, набор, keep, потолок)]; keep: None | ("pause", мин, только стоп) |
    ("h9r", сторона, клетка) | ("g08",)."""
    idx = grid_index()
    out = []
    for d in sorted(glob.glob(os.path.join(HOME, "tmp-p07/read-[ab]-*")) + glob.glob(os.path.join(HOME, "tmp-p07/h9-[ab]-*"))):
        if not os.path.isfile(os.path.join(d, "ps.json")):
            continue
        name = os.path.basename(d)
        v = json.load(open(os.path.join(d, "ps.json"), encoding="utf-8"))["variants"][0]
        form, set_ = v["form"], v["set"]
        side = name.split("-")[1]
        base = f"p07{side}-base"
        if name.startswith("read-"):
            # имя прогона = каталог клетки без точек (read-b-h2-00136 ↔ p07b-h2-0.0136); форма — сверка
            cand = [cd for (pre, f, s), cds in idx.items() if f == form for cd in cds
                    if cd.replace(".", "") == f"p07{side}-{name[7:]}"]
            assert len(cand) == 1, (name, form, set_, cand)
            out.append((name, cand[0], form, set_, None, 0))
            continue
        rest = name[len(f"h9-{side}-"):]
        if rest == "pause0":
            out.append((name, base, form, set_, None, 0))
        elif rest.startswith("cap"):
            out.append((name, base, form, set_, None, int(rest[3:])))
        elif rest.startswith("pauseStop"):
            out.append((name, base, form, set_, ("pause", int(rest[9:]), True), 0))
        elif rest.startswith("pause"):
            out.append((name, base, form, set_, ("pause", int(rest[5:]), False), 0))
        elif rest.startswith("h9r-p07b-base-"):
            cell = rest[len("h9r-p07b-base-"):]
            out.append((name, base, form, set_, ("h9r", side, cell), _cap(cell)))
        elif rest.startswith("h9r-p07b-"):
            assert rest.endswith("-k1"), name
            cd = rest[len("h9r-"):-3]
            assert os.path.isdir(os.path.join(JUL, "b5", cd)), (name, cd)
            out.append((name, cd, form, set_, ("h9r", side, "k1"), 0))
        elif rest.startswith("h9r-"):
            cell = rest[4:]
            out.append((name, base, form, set_, ("h9r", side, cell), _cap(cell)))
        else:
            raise AssertionError(name)
    # главный и соседи, П-02, П-05 — клетки `p07-all-jul.py`
    names = {"p07m-main": "m-main", "p07m-take": "m-take", "p07m-btc1h": "m-btc1h", "p07m-nofilter": "m-nofilter",
             "p02-h10-before": "p02-g105"}
    for c, _ in JA.MAIN_EXTRA:
        out.append((names[c[0]], c[0], pc.label(c), c[8], None, 0))
    g = JA.G86[0]
    out.append(("p02-g86", g[0], pc.label(g), g[8], None, 0))
    out.append(("p02-g08", "p07m-main", MAIN_FORM, pc.BASE_SET, ("g08",), 0))
    for c, _ in JA.p05_cells():
        out.append((c[8], c[0], pc.label(c), c[8], None, 0))
    assert len({r[0] for r in out}) == len(out), "повтор имени"
    return out


def _cap(cell):
    hr = _mods()[2]
    return hr.CELL_K1CAP.get(cell, 0)


_M = {}


def _mods():
    if not _M:
        pr = JR.load_mod("p07-read.py", "p07read")
        ph = JR.load_mod("p07-h9h10.py", "p07base")
        hr = JR.load_mod("p07-h9r-h14.py", "p07h9r")
        ph.HOMES = {"jul": JUL}
        kn = pr.load_mod(pr.KN_PATH, "kn")
        orig = JR.patch_july(kn)
        _M.update(pr=pr, ph=ph, hr=hr, kn=kn, orig=orig)
    return _M["pr"], _M["ph"], _M["hr"], _M["kn"]


# ---------- счёт одного варианта ----------
def g08_filter(src, dst):
    """Сделки главной формы, чья стена на круглом числе — как `p02-variant-filter.py filter` (f-g08): связь сделка →
    подход по arm_ms = t0 (мс), bid, возраст ≥ 45 мин, ровно один подход; round_zeros(price_tick) ≥ 2."""
    for day in JR.JUL_DAYS:
        s = os.path.join(src, day, pc.BASE_SET)
        if not os.path.isdir(s):
            continue
        t = os.path.join(dst, day, pc.BASE_SET)
        os.makedirs(t, exist_ok=True)
        for fn in os.listdir(s):
            if fn != "rounds.csv":
                shutil.copy(os.path.join(s, fn), os.path.join(t, fn))
        with open(os.path.join(s, "rounds.csv"), encoding="utf-8") as fh:
            lines = fh.read().splitlines(keepends=True)
        head = [l for l in lines if l.startswith("#")]
        body = [l for l in lines if not l.startswith("#")]
        rows_ = list(csv.DictReader(body))
        arm = {}
        for sym in {r["symbol"] for r in rows_}:
            ap = os.path.join(JUL, "study/approaches/D20", day, f"approaches-{sym}.csv")
            if not os.path.exists(ap):
                continue
            with open(ap, encoding="utf-8") as fh:
                for a in csv.DictReader(l for l in fh if not l.startswith("#")):
                    if a["side"] == "bid" and int(a["age_ms"]) >= PV.MIN_AGE_MS:
                        arm.setdefault((sym, int(a["arm_ms"])), []).append(a)
        keep = []
        for line, r in zip(body[1:], rows_):
            if r["form"] != MAIN_FORM:
                continue
            c = arm.get((r["symbol"], int(r["t0_ns"]) // 1_000_000), [])
            if len(c) == 1 and PV.round_zeros(int(c[0]["price_tick"])) >= 2:
                keep.append(line)
        with open(os.path.join(t, "rounds.csv"), "w", encoding="utf-8", newline="") as fh:
            fh.writelines(head + body[:1] + keep)


def run_row(row):
    name, cell_dir, form, set_, keep, cap = row
    pr, ph, hr, kn = _mods()
    d = os.path.join(OUT_ROOT, name)
    out_dir = os.path.join(d, "jul")
    os.makedirs(out_dir, exist_ok=True)
    cmd = ["python3", pr.BUSY_REPLAY, os.path.join(JUL, "b5", cell_dir), out_dir, "--sets", set_]
    n_keep = None
    try:
        if keep and keep[0] in ("pause", "h9r"):
            side = cell_dir[3]
            ph.base_dir_for = lambda _v, cd=cell_dir: cd
            ph.FORM_OF[side] = form
            ph.SET_ = set_
            k = ph.simulate_pause(side, keep[1], keep[2]) if keep[0] == "pause" else hr.simulate(ph, side, keep[2])
            n_keep = len(k)
            kp = os.path.join(d, "keep.csv")
            ph.write_keep(k, kp)
            cmd += ["--keep", kp]
        pr.run(cmd)
        if keep and keep[0] == "g08":
            filt = os.path.join(d, "jul-g08")
            shutil.rmtree(filt, ignore_errors=True)
            g08_filter(out_dir, filt)
            out_dir = filt
        closes = J9R.portfolio_sim(pr, d, set_, form, out_dir, cap)
        sym = pr.symbol_map({"jul": out_dir}, form, set_)
    except AssertionError as e:
        return name, {"cell_dir": cell_dir, "form": form, "set": set_, "undefined": str(e)}, None
    units, oor = pr.daily_series(closes, JR.JUL_DAYS)
    res = {"cell_dir": cell_dir, "form": form, "set": set_, "keep": list(keep) if keep else None, "max_pos": cap,
           "n_signals_kept": n_keep, "n_trades": len(closes), "n_out_of_month": oor, "usd": pr.block_boot(units),
           "kpi": JR.verdict_one_month(pr, kn, closes, sym) if closes else None}
    return name, res, units


def gates(rs):
    bad = []
    for f, want in (("jall-gate.status", "ВОРОТА 03.08: ok"), ("jall-vgate.txt", "ВОРОТА VPS: ok")):
        p = os.path.join(HOME, "tmp-p07", f)
        if not os.path.exists(p) or want not in open(p, encoding="utf-8").read():
            bad.append(f"{f}: не ok")
    for cd in sorted({r[1] for r in rs}):
        miss = [x for x in JR.JUL_DAYS if not os.path.exists(os.path.join(JUL, "b5", cd, x, ".done"))]
        if miss:
            bad.append(f"{cd}: нет {len(miss)} суток")
    return bad


WORKER_GB = 3.0  # TK-019 14:55: пик воркера читателя jall 2,9 ГБ (RSS + своп), замер инженера
KEEP_GB = 2.0  # не отдавать читателям последние 2 ГБ MemAvailable (заморозка деки утром 28.09)


def auto_jobs():
    """Число процессов по свободной памяти сейчас, а не константой (TK-019)."""
    avail_gb = 0.0
    with open("/proc/meminfo") as f:
        for line in f:
            if line.startswith("MemAvailable:"):
                avail_gb = int(line.split()[1]) / 1048576
    return max(1, min(os.cpu_count() or 1, int((avail_gb - KEEP_GB) // WORKER_GB)))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=0,
                    help="0 = по свободной памяти деки (MemAvailable − 2 ГБ) / WORKER_GB, не больше ядер")
    ap.add_argument("--only", help="через запятую — только эти имена (проба, без ворот)")
    ap.add_argument("--list", action="store_true", help="только список вариантов")
    a = ap.parse_args()
    rs = rows()
    if a.list:
        for r in rs:
            print(r[0], r[1], r[3], r[4], r[5])
        print("ИТОГО", len(rs))
        return
    if a.only:
        only = set(a.only.split(","))
        rs = [r for r in rs if r[0] in only]
    else:
        bad = gates(rs)
        if bad or not JR.data_gate(verbose=False):
            print("ворота не пройдены — счёт не начат:", "; ".join(bad[:10]) or "данные июля (p07-jul-read --data-gate)")
            sys.exit(1)
    os.makedirs(OUT_ROOT, exist_ok=True)
    pr, ph, hr, kn = _mods()
    if any(r[4] and r[4][0] == "h9r" and "f" in r[4][2] for r in rs):
        jp = os.path.join(J9R.OUT_ROOT, "h1-join-jul.json")
        if not os.path.exists(jp):
            jp = J9R.h1_join_july()
        d = json.load(open(jp, encoding="utf-8"))
        ok = d["unmatched"] == 0 and d["ambiguous"] == 0
        share = {(r[1], r[2], r[3]): r[7] for r in d["rows"]}

        def load_jul():
            assert ok, "H1: соединение с кэшем июля не 100 %"
            return share
        hr.load_h1_share = load_jul
    res = {"_meta": {"task": "TK-018", "days": [JR.JUL_DAYS[0], JR.JUL_DAYS[-1]], "h_days": 5, "deposit": 2500,
                     "position": 500, "drop": JR.DROP, "created_utc": dt.datetime.now(dt.timezone.utc).isoformat()}}
    units_by = {}
    if a.jobs <= 0:
        a.jobs = auto_jobs()
    print(f"--jobs {a.jobs}", flush=True)
    with mp.get_context("fork").Pool(a.jobs) as pool:
        for name, r, units in pool.imap_unordered(run_row, rs):
            res[name] = r
            if units is not None:
                units_by[name] = units
    # Δ$ к главному по суткам (как П-05: главный — база сравнения В-121)
    if "m-main" in units_by:
        ub = units_by["m-main"]
        for n, u in units_by.items():
            if n != "m-main":
                res[n]["diff_main_usd"] = pr.block_boot([x - y for x, y in zip(u, ub)])
    with open(a.out, "w", encoding="utf-8", newline="") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=1)
    print(a.out)
    print("готово")


if __name__ == "__main__":
    main()
