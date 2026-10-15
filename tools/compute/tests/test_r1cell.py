"""TK-250: r1cell.py = tk064-r1cells.py (FAM) + tk202-r1x.py (ctx) на синтетическом пуле — файлы клеток байт в байт; хеш в .r1spec = хеш плана."""
import csv, importlib.util, json, os, random, subprocess, sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
TOOLS = os.path.normpath(os.path.join(HERE, ".."))
ROOT = os.path.normpath(os.path.join(TOOLS, "..", ".."))
R1CELL = os.path.join(TOOLS, "r1cell.py")
TK064 = os.path.join(TOOLS, "archive", "tk064-r1cells.py")
TK202 = os.path.join(TOOLS, "tk202-r1x.py")
PLAN = os.path.join(ROOT, "docs", "registry", "plan_cells.jsonl")
SET = "t-bid-btc4h-q1"


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


r1cell = load(R1CELL, "r1cell_mod")
tk064 = load(TK064, "tk064_mod")
COLS = ["cancel_life_lots", "wall_add_max_lots", "front_add_max_lots", "obi50_bp", "best_move_1s_cbps"]


def make_pool(root, n_jan=70, n_feb=50):
    rnd = random.Random(250)
    for mon, n, day in (("jan", n_jan, "2026-01-10"), ("feb", n_feb, "2026-02-10")):
        d = os.path.join(root, f"m-{mon}", "signals", day, SET)
        os.makedirs(d)
        os.makedirs(os.path.join(root, f"m-{mon}", "lvl"))
        sig_h = ["symbol", "day_utc", "signal_index", "t0_ns", "price_tick"] + COLS
        rnd_h = ["symbol", "day_utc", "signal_index", "t0_ns", "net_bps", "reason", "exit_ns", "qty", "form"]
        with open(os.path.join(d, "signals.csv"), "w", newline="") as fs, open(os.path.join(d, "rounds.csv"), "w", newline="") as fr, \
                open(os.path.join(root, f"m-{mon}", "lvl", f"{day}.csv"), "w", newline="") as fl:
            ws, wr, wl = csv.writer(fs), csv.writer(fr), csv.writer(fl)
            ws.writerow(sig_h)
            wr.writerow(rnd_h)
            wl.writerow(["symbol", "arm_ms", "price_tick", "size_at_arm"])
            for i in range(n):
                t0 = 1_700_000_000_000_000_000 + i * 70_000_000_000
                tick = 100 + i
                vals = [
                    "" if rnd.random() < 0.1 else rnd.randint(0, 900),                     # cancel_life_lots: бывают пустые
                    rnd.randint(0, 500),                                                   # wall_add_max_lots
                    0 if rnd.random() < 0.8 else rnd.randint(1, 50),                       # front_add_max_lots: масса в нуле
                    rnd.randint(-5000, 5000),                                              # obi50_bp: не лоты
                    rnd.randint(-300, 300),                                                # best_move_1s_cbps
                ]
                ws.writerow(["BTCUSDT", day, i, t0, tick] + vals)
                wr.writerow(["BTCUSDT", day, i, t0, rnd.randint(-40, 40), "take", t0 + 5, 1, "f"])
                if i % 11:   # у части сигналов нет size_at_arm → доли не определены
                    wl.writerow(["BTCUSDT", t0 // 1_000_000, tick, rnd.randint(50, 400)])


def write_spec(path, rows):
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["hash", "hyp", "col", "sign", "keep_q", "ctx_axis", "ctx_q"])
        w.writerows(rows)


def run(script, *args):
    r = subprocess.run([sys.executable, script, *args], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    return r


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    root = tmp_path_factory.mktemp("w")
    pool = str(root / "pool")
    make_pool(pool)
    return root, pool


def test_col_class_matches_tk064_lots():
    for c in tk064.FEATS:
        if c in {"cancel_60m_lots", "cancel_3s_lots", "cancel_1s_lots"}:
            continue   # BIN-клетки tk064 (не в этом модуле): там сырое значение, у R1X (tk202) — доля
        assert (r1cell.col_class(c) == "lots") == (c in tk064.LOTS), c


def test_fam_table_matches_tk064():
    for fam, (cols, sign) in r1cell.FAM.items():
        assert (cols, 1 if sign == "hi" else -1) == (tk064.FAM[fam][0], tk064.FAM[fam][2]), fam


def test_fam_cells_equal_tk064_byte_for_byte(world):
    root, pool = world
    out064 = str(root / "o064")
    run(TK064, "apply", "--pool", pool, "--out", out064, "--levels", "80,67,50,33,20")
    rows = [(f"h{i}", h, col, sign, q, "", "") for i, (h, col, sign, name, q) in enumerate([
        ("Г-39", "cancel_life_lots", "lo", "g39", 20), ("Г-39", "cancel_life_lots", "lo", "g39", 50), ("Г-39", "cancel_life_lots", "lo", "g39", 80),
        ("Г-04", "wall_add_max_lots", "hi", "g04", 33), ("Г-35", "front_add_max_lots", "lo", "g35", 67),   # q ниже: масса в нуле → порог по ненулевым
        ("Г-139", "obi50_bp", "lo", "g139", 20)])]
    names = ["g39-k20", "g39-k50", "g39-k80", "g04-k33", "g35-k67", "g139-k20"]
    spec = str(root / "s.r1spec")
    write_spec(spec, rows)
    out = str(root / "onew")
    run(R1CELL, "apply", "--spec", spec, "--pool", pool, "--out", out, "--months", "feb")
    assert "front_add_max_lots" in json.load(open(os.path.join(out, "summary.json")))["zero_mass_columns"]
    for (h, *_), name in zip(rows, names):
        old = open(os.path.join(out064, "cells", "feb", name + ".csv"), "rb").read()
        new = open(os.path.join(out, "cells", "feb", h + ".csv"), "rb").read()
        assert old == new and len(old) > 60, name


def test_ctx_cells_and_jan_equal_tk202_byte_for_byte(world):
    root, pool = world
    t46 = str(root / "t46")
    for mon, mo, day in (("jan", "01", "2026-01-10"), ("feb", "02", "2026-02-10")):
        d = os.path.join(t46, mon, "home", "alpha", "epochs", f"e-{mon}", "study", "regime")
        os.makedirs(d)
        with open(os.path.join(d, day + ".csv"), "w", newline="") as f:
            w = csv.writer(f)
            w.writerow(["minute_ms", "pool_ret_1h_bps", "btc_ret_1h_bps"])
            for i in range(80):
                w.writerow([(1_700_000_000_000 + i * 60_000) - (1_700_000_000_000 % 60_000), (i * 7) % 90 - 45, (i * 5) % 70 - 35])
    thr = str(root / "thr.json")
    ctx = {"q20": -30, "q40": -10, "q60": 10, "q80": 30}
    json.dump({"ctx": {"pool1h": {"2026-01": ctx, "2026-02": ctx}, "btc1h": {"2026-01": ctx, "2026-02": ctx}}}, open(thr, "w"))
    spec = str(root / "x.r1spec")
    write_spec(spec, [("c1", "Г-78", "best_move_1s_cbps", "lo", 50, "pool1h", "q3"), ("c2", "Г-55", "best_move_1s_cbps", "lo", 67, "btc1h", "q5"),
                      ("c3", "Г-28", "wall_add_max_lots", "lo", 33, "", ""), ("c4", "Г-28", "cancel_life_lots", "hi", 80, "", "")])
    o202, onew = str(root / "o202"), str(root / "onew2")
    run(TK202, "apply", "--spec", spec, "--pool", pool, "--t46", t46, "--thr", thr, "--out", o202)
    run(R1CELL, "apply", "--spec", spec, "--pool", pool, "--t46", t46, "--thr", thr, "--out", onew, "--months", "jan,feb")
    for mon in ("jan", "feb"):
        for h in ("c1", "c2", "c3", "c4"):
            a = open(os.path.join(o202, "cells", mon, h + ".csv"), "rb").read()
            b = open(os.path.join(onew, "cells", mon, h + ".csv"), "rb").read()
            assert a == b, (mon, h)
    assert len(open(os.path.join(onew, "cells", "feb", "c1.csv")).read().splitlines()) > 1


def test_months_filter_writes_only_requested(world):
    root, pool = world
    spec = str(root / "m.r1spec")
    write_spec(spec, [("m1", "Г-39", "cancel_life_lots", "lo", 50, "", "")])
    out = str(root / "om")
    run(R1CELL, "apply", "--spec", spec, "--pool", pool, "--out", out, "--months", "feb")
    assert os.listdir(os.path.join(out, "cells")) == ["feb"]


def test_r1ext_name():
    assert r1cell.r1ext_name("g39", "cancel_life_lots", 20) == "g39-k20"
    assert r1cell.r1ext_name("g68", "obi5_bp", 33) == "g68-obi5_bp-k33"


@pytest.mark.skipif(not os.path.isfile(PLAN), reason="нет docs/registry/plan_cells.jsonl")
def test_spec_hash_is_plan_hash(tmp_path):
    out = str(tmp_path / "s.r1spec")
    run(R1CELL, "spec", "--plan", PLAN, "--which", "all", "--hyp", "Г-39,Г-04", "--out", out)
    spec = list(csv.DictReader(open(out, encoding="utf-8")))
    plan = {}
    for line in open(PLAN, encoding="utf-8"):
        d = json.loads(line)
        plan[d["cell_hash"]] = d
    assert len(spec) == 18 and {r["hyp"] for r in spec} == {"Г-39", "Г-04"}
    for r in spec:
        d = plan[r["hash"]]
        assert d["hyp"] == r["hyp"] and json.loads(d["axes"]) == {"col": r["col"], "keep_q": r["keep_q"]}
    assert {r["sign"] for r in spec if r["hyp"] == "Г-39"} == {"lo"} and {r["sign"] for r in spec if r["hyp"] == "Г-04"} == {"hi"}


@pytest.mark.skipif(not os.path.isfile(PLAN), reason="нет docs/registry/plan_cells.jsonl")
def test_spec_x_skips_r1ext_done(tmp_path):
    out = str(tmp_path / "s.r1spec")
    run(R1CELL, "spec", "--plan", PLAN, "--out", out)   # по умолчанию — как tk202: вне r1ext и ctx
    spec = list(csv.DictReader(open(out, encoding="utf-8")))
    assert spec and not any(r["hyp"] in ("Г-39", "Г-04") for r in spec)
