#!/usr/bin/env python3
"""TK-493, П-38 §3.4 (стадия G1): тесты Gate в универсуме C2 с ИЗВЕСТНЫМ ответом на синтетике. Запуск на Deck:
    cd <дерево>/tools/compute/statarb/xdl9/conv && ~/xdl9/venv/bin/python test_conv_gate.py [имя_теста ...]
Реальные панели не читаются (только синтетика во временных каталогах). Отдельный файл от test_conv.py (27 тестов ядра, их не трогает): те же приёмы, свой запуск.
ЗАВИСИМОСТЬ ОТ СОСЕДНИХ МОДУЛЕЙ (d12 ревизии 07.11): g07 импортирует xdl9/asset_lib.py, asset_class.py, asset_cands.py (через conv_gate: `conv/..`), g09 - tools/xdl/gate_manifest.py
(`conv/../../../../xdl`); запускать из развёртки `git archive` с каталогами tools/compute/statarb и tools/xdl (на Deck: ~/xdl9/c2/code_win), не из копии одного conv/.
Тесты: манифест (валидный и красные случаи), переключатель Gate в универсуме и CLI, побайтное совпадение с миром без Gate при --no-gate, детерминизм при включённом Gate,
рынок gate_tradfi, множитель оборота и G-C2-7 для Gate (>= 12 символов, сверка с архивом, красные случаи), таблица пар Gate по R2' (вердикты, дубли, пред-рынок, классы),
фандинг Gate по каталогу манифеста, члены подсказки H4, проверка манифеста по контракту (окно не читается)."""
import csv
import gzip
import hashlib
import json
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import conv_gate as CG          # noqa: E402
import conv_guard as G          # noqa: E402
import conv_world as W          # noqa: E402

XDL_DIR = os.path.abspath(os.path.join(HERE, "..", "..", "..", "..", "xdl"))
OK = []
FIRST = 2465 * 1440
NF = 14 * 1440


def test(fn):
    OK.append(fn)
    return fn


def sha(path):
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


# ------------------------------------------------------------------------------------------------ синтетический мир с Gate
def write_sap(path, o, c, first=FIRST):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"SAP1" + struct.pack("<I", 0) + struct.pack("<qQ", first, len(c)))
        f.write(np.asarray(o, "<f8").tobytes() + np.asarray(c, "<f8").tobytes())


def write_manifest(d, mult=1.0, label="first_trade", skip=(), extra=None, nsym=None):
    """Манифест gate_ready с sha256 справочника и списка панелей из каталога мира d (создаёт их, если нет)."""
    ip = os.path.join(d, "inst_gate", "gate.csv")
    sp = os.path.join(d, "gx", "sha256-gate.txt")
    os.makedirs(os.path.dirname(ip), exist_ok=True)
    os.makedirs(os.path.dirname(sp), exist_ok=True)
    if not os.path.isfile(ip):
        open(ip, "w", encoding="utf-8").write("exchange,symbol\ngate,G0_USDT\n")
    if not os.path.isfile(sp):
        open(sp, "w", encoding="utf-8").write("x  G0_USDT.sap\n")
    pd = os.path.join(d, "panels", "gate")
    n = len([x for x in os.listdir(pd) if x.endswith(".sap")]) if os.path.isdir(pd) else 0
    kv = [("%s=ok" % k) for k in CG.MANIFEST_CHECKS if k not in skip]
    kv += ["symbols=%d" % (n if nsym is None else nsym), "instruments_sha256=%s" % sha(ip), "panels_manifest_sha256=%s" % sha(sp),
           "units_turnover_mult_on_file_v=%s" % mult, "open_semantics_label=%s" % label, "funding_dir=%s" % os.path.join(d, "gate_funding"), "raw_root=%s" % os.path.join(d, "raw")]
    kv += list(extra or [])
    open(os.path.join(d, "gate_ready"), "w", encoding="utf-8").write("# gate_ready (тест)\n" + "\n".join(kv) + "\n")


def add_gate(d, n_assets=6, seed=11, days=14):
    """К миру write_world (aex, bex, cex; активы A0..): панели Gate G<i>_USDT (всплески в ноге), asset_pairs_gate.csv (+meta), фандинг, манифест; пара Gate-акций (gate_tradfi)."""
    rng = np.random.default_rng(seed)
    n = days * 1440
    rows = []
    cols = CG.PAIR_COLS + ["gate_note"]
    base_rows = {r["key"]: r for r in csv.DictReader(open(os.path.join(d, "asset_pairs.csv"), encoding="utf-8"))}
    for ai in range(n_assets):
        base = 50.0 + 20 * ai
        common = np.cumsum(rng.normal(0, 1.2, n))
        lg = common + rng.normal(0, 0.3, n)
        for _ in range(10):
            st = int(rng.integers(1500, n - 200))
            ln = int(rng.integers(1, 40))
            lg[st:st + ln] += float(rng.choice([-1, 1]) * rng.uniform(30, 200))
        c = np.round(base * np.exp(lg / 1e4), 2)
        o = np.concatenate([[c[0]], c[:-1]])
        gaps = rng.random(n) < 0.02
        c[gaps] = np.nan
        o[gaps] = np.nan
        write_sap(os.path.join(d, "panels", "gate", "G%d_USDT.sap" % ai), o, c)
        for ex in ("aex", "bex", "cex"):
            rows.append(dict(key="A%d" % ai, scope="между биржами", big1="крипта", big2="крипта", class_match=1, ex1=ex, sym1="SYM%dUSDT" % ai, role1="main", ex2="gate", sym2="G%d_USDT" % ai,
                             role2="main", quote1="USDT", quote2="USDT", n_ret=5000, S="0.01", verdict="одно", c1d2=1, gate_note=""))
    # Gate-акции: пара gate x aex класса «акции+ETF» (рынок gate_tradfi) и пара с c1d2 = 0 (не должна попасть в универсум)
    rows.append(dict(key="EQ1", scope="между биржами", big1="акции+ETF", big2="акции+ETF", class_match=1, ex1="aex", sym1="EQ1USDT", role1="main", ex2="gate", sym2="EQ1_USDT", role2="main",
                     quote1="USDT", quote2="USDT", n_ret=5000, S="0.01", verdict="одно", c1d2=1, gate_note=""))
    rows.append(dict(key="A0", scope="между биржами", big1="крипта", big2="крипта", class_match=1, ex1="aex", sym1="SYM0USDT", role1="main", ex2="gate", sym2="G1_USDT", role2="dup",
                     quote1="USDT", quote2="USDT", n_ret=5000, S="0.01", verdict="одно", c1d2=0, gate_note=""))
    pp = os.path.join(d, "asset_pairs_gate.csv")
    with open(pp, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols, lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in cols})
    json.dump(dict(pairs_sha256=sha(pp)), open(os.path.join(d, "asset_pairs_gate.meta.json"), "w"))
    # фандинг Gate (каталог манифеста gate_funding)
    fd = os.path.join(d, "gate_funding")
    os.makedirs(fd, exist_ok=True)
    for ai in range(n_assets):
        with gzip.open(os.path.join(fd, "G%d_USDT.csv.gz" % ai), "wt", encoding="utf-8", newline="") as f:
            f.write("time_ms,rate,interval_h\n")
            for k in range(1, 6):
                f.write("%d,%.6f,8\n" % (G.GRID0_MS + (FIRST + k * 480 + 7) * 60_000, 0.0001 * k))
    write_manifest(d)
    return len(rows)


def write_empty_gate_pairs(d):
    """Пустая таблица пар Gate + meta (для тестов, где нужен только манифест: оборот, фандинг)."""
    pp = os.path.join(d, "asset_pairs_gate.csv")
    with open(pp, "w", encoding="utf-8", newline="") as f:
        f.write(",".join(CG.PAIR_COLS + ["gate_note"]) + chr(10))
    json.dump(dict(pairs_sha256=sha(pp)), open(os.path.join(d, "asset_pairs_gate.meta.json"), "w"))


def make_world(n_assets=6):
    d = tempfile.mkdtemp()
    sys.path.insert(0, HERE)
    import test_conv as T
    T.write_world(d, n_assets=n_assets)
    return d, T


def cfg_test(d):
    G.configure(mode="test", params_paths=(), test_root=d)


# ------------------------------------------------------------------------------------------------ манифест
@test
def g01_manifest_valid_and_red():
    d, _ = make_world()
    add_gate(d)
    ok, why, info = CG.manifest_valid(d, expect_open_label="first_trade")
    assert ok and not why and info["mult"] == 1.0 and info["symbols"] == 6, (ok, why, info)
    # красный: любая строка не ok
    for k in CG.MANIFEST_CHECKS:
        write_manifest(d, skip=(k,))
        ok, why, _ = CG.manifest_valid(d)
        assert not ok and any(k in w for w in why), (k, why)
    write_manifest(d)
    # красный: нет файла
    os.rename(os.path.join(d, "gate_ready"), os.path.join(d, "gate_ready.x"))
    assert not CG.manifest_valid(d)[0]
    os.rename(os.path.join(d, "gate_ready.x"), os.path.join(d, "gate_ready"))
    # красный: старый маркер «в свободной форме» (как был на K0) - строки ok нет
    open(os.path.join(d, "gate_ready"), "w", encoding="utf-8").write("gate_ready: проверки пройдены\ncontracts=1072\nrows_csv=19881344\n")
    ok, why, _ = CG.manifest_valid(d)
    assert not ok and len(why) >= 6, why
    # красный: устаревший манифест (справочник изменён), число панелей, множитель, метка
    write_manifest(d)
    open(os.path.join(d, "inst_gate", "gate.csv"), "a", encoding="utf-8").write("gate,NEW_USDT\n")
    ok, why, _ = CG.manifest_valid(d)
    assert not ok and any("instruments_sha256" in w for w in why), why
    write_manifest(d)
    write_manifest(d, nsym=7)
    assert not CG.manifest_valid(d)[0]
    write_manifest(d, mult="abc")
    assert not CG.manifest_valid(d)[0]
    write_manifest(d, mult=0)
    assert not CG.manifest_valid(d)[0]
    write_manifest(d, label="chain")
    ok, why, _ = CG.manifest_valid(d, expect_open_label="first_trade")
    assert not ok and any("семантики open" in w for w in why), why
    # старый K0 (conv_guard.write_k0) читает тот же файл: валидный манифест даёт gate_ready_valid = true
    write_manifest(d)
    G.configure(mode="calib", require_k0=False)
    os.environ["C2_HOME"] = tempfile.mkdtemp()
    try:
        rec = G.write_k0(os.path.join(d, "gate_ready"), ())
        assert rec["gate_ready_valid"] is True and rec["gate_nsym"] == "6", rec
    finally:
        os.environ.pop("C2_HOME", None)
        G.configure(mode="calib")


# ------------------------------------------------------------------------------------------------ универсум и переключатель
@test
def g02_universe_toggle_and_markets():
    d, _ = make_world()
    base = W.World(d, test=True, gate="off").universe(check_sha=False)
    nbase = len(base)
    add_gate(d)
    cfg_test(d)
    w_off = W.World(d, test=True, gate="off")
    u_off = w_off.universe(check_sha=False)
    assert [p["pid"] for p in u_off] == [p["pid"] for p in base] and not any("gate" in (p["exA"], p["exB"]) for p in u_off)
    w_auto = W.World(d, test=True)
    u = w_auto.universe(check_sha=False)
    gp = [p for p in u if "gate" in (p["exA"], p["exB"])]
    assert w_auto.gate_state()["active"] and len(u) == nbase + 6 * 3 + 1 and len(gp) == 19, (len(u), nbase, len(gp))
    crypto = [p for p in gp if p["cls"] == "crypto"]
    eq = [p for p in gp if p["cls"] == "eq"]
    assert len(crypto) == 18 and all(p["market"] == "24x7" and w_auto.in_main_series(p) for p in crypto)
    assert len(eq) == 1 and eq[0]["market"] == "gate_tradfi" and not w_auto.in_main_series(eq[0]), eq
    assert not any(p["symB"] == "G1_USDT" and p["exA"] == "aex" and p["symA"] == "SYM0USDT" for p in u), "пара c1d2 = 0 попала в универсум"
    assert all(p["exA"] < p["exB"] or p["exA"] == p["exB"] for p in gp) and all(p["cell"].endswith("/crypto") or p["cell"].endswith("/eq") for p in gp)
    # одна общая нога Gate у трёх пар актива: компонента связности объединяет их
    comps = W.components([p for p in u if in_main(w_auto, p) and p["cls"] == "crypto" and p["key"] == "A0"])
    assert len(comps) == 1
    # красные: невалидный манифест - Gate вне универсума (auto), отказ (on)
    write_manifest(d, skip=("units",))
    w_bad = W.World(d, test=True)
    u_bad = w_bad.universe(check_sha=False)
    st = w_bad.gate_state()
    assert not st["active"] and any("units" in x for x in st["reasons"]) and [p["pid"] for p in u_bad] == [p["pid"] for p in base]
    try:
        W.World(d, test=True, gate="on").universe(check_sha=False)
        raise AssertionError("--with-gate принят при невалидном манифесте")
    except SystemExit as e:
        assert "Gate невалиден" in str(e.code), e.code
    # красный: таблица пар Gate изменена после записи meta (sha256)
    write_manifest(d)
    open(os.path.join(d, "asset_pairs_gate.csv"), "a", encoding="utf-8").write("\n")
    st = W.World(d, test=True).gate_state()
    assert not st["active"] and any("sha256 таблицы" in x for x in st["reasons"]), st["reasons"]


def in_main(w, p):
    return w.in_main_series(p)


# ------------------------------------------------------------------------------------------------ CLI: --no-gate побайтно, детерминизм с Gate
def run_cli(args, env=None):
    e = dict(os.environ)
    e.update(env or {})
    return subprocess.run([sys.executable, os.path.join(HERE, "conv_run.py")] + args, capture_output=True, text=True, env=e)


def sha_tree(d):
    h = {}
    for root, _, fs in os.walk(d):
        for f in sorted(fs):
            if f.endswith((".npy", ".json", ".csv")) and f not in ("series_summary.json", "meta.json"):
                p = os.path.join(root, f)
                h[os.path.relpath(p, d)] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return h


@test
def g03_nogate_byte_identical_and_gate_deterministic():
    d, _ = make_world()
    d0 = tempfile.mkdtemp()
    shutil.rmtree(d0)
    shutil.copytree(d, d0)                                              # мир без Gate (как до G1)
    add_gate(d)
    cf = os.path.join(d, "combos.json")
    json.dump([[25.2, 3, 1, 120, 2.0], [40.0, 3, 3, 360, None], [25.2, 5, 1, 1440, 2.0]], open(cf, "w"))

    def one(world, extra, th=1):
        out = tempfile.mkdtemp()
        base = ["--test-world", world, "--out", out, "--last-day", "2478", "--threads", str(th), "--nb", "240", "--no-turn"] + extra
        r = run_cli(["series"] + base)
        assert r.returncode == 0, r.stderr[-800:]
        r = run_cli(["grid"] + base + ["--tag", "t", "--combos", cf, "--trades-out"])
        assert r.returncode == 0, r.stderr[-800:]
        return out, r.stdout
    o_old, so = one(d0, [])
    o_ng, sg = one(d, ["--no-gate"])
    h_old, h_ng = sha_tree(o_old), sha_tree(o_ng)
    assert len(h_old) > 8 and h_old == h_ng, [k for k in h_ng if h_ng[k] != h_old.get(k)]
    assert "вне универсума" in sg
    o1, s1 = one(d, ["--with-gate"], th=1)
    o4, _ = one(d, [], th=4)                                            # auto: манифест валиден - Gate включён
    h1, h4 = sha_tree(o1), sha_tree(o4)
    assert "в универсуме" in s1 and h1 == h4 and h1 != h_ng, "Gate включён: потоки 1/4 равны, выход отличается от мира без Gate"
    pids = json.load(open(os.path.join(o1, "calib", "grid_t", "crypto", "pids.json")))
    assert sum(1 for p in pids if "gate" in p) == 18 and len(pids) == len(json.load(open(os.path.join(o_ng, "calib", "grid_t", "crypto", "pids.json")))) + 18
    ntr = open(os.path.join(o1, "calib", "grid_t", "crypto", "trades.csv")).read().split(chr(10))
    gate_tr = [ln for ln in ntr[1:] if ln and "gate" in ln.split(",")[1]]
    assert gate_tr, "в синтетике нет сделок Gate - проверка пуста"
    print("   --no-gate = мир без Gate (%d файлов побайтно); Gate включён: потоки 1/4 равны, пар Gate 18" % len(h_old))


@test
def g04_cli_flags():
    d, _ = make_world()
    add_gate(d)
    out = tempfile.mkdtemp()
    base = ["--test-world", d, "--out", out, "--last-day", "2478", "--no-turn"]
    r = run_cli(["series"] + base + ["--with-gate", "--no-gate"])
    assert r.returncode == 2, (r.returncode, r.stderr[-300:])
    write_manifest(d, skip=("rows",))
    r = run_cli(["series"] + base + ["--with-gate"])
    assert r.returncode != 0 and "Gate невалиден" in (r.stderr + r.stdout), (r.returncode, r.stderr[-300:])
    r = run_cli(["series"] + base)                                      # auto: невалиден - вне универсума, счёт идёт
    assert r.returncode == 0 and "вне универсума" in r.stdout, (r.returncode, r.stdout[-300:], r.stderr[-300:])


# ------------------------------------------------------------------------------------------------ единицы оборота Gate, G-C2-7
def write_gate_raw(d, n_sym=14, quanto_list=(0.0001, 0.01, 1, 10, 11, 1000, 0.1, 100, 10000000, 0.001, 1, 10, 0.01, 100), seed=5, bug_v=False):
    """Сырьё Gate: raw/gate/candles/<SYM>.csv.gz (v в базовых единицах = контракты × quanto, как пишет gate.py), raw/gate/_raw/candlesticks_1m/202610/<NAME>.csv.gz
    (t(с), контракты, close, high, low, open), raw/gate/instruments.csv. bug_v: в файле свечей v = контракты (загрузчик не умножил)."""
    rng = np.random.default_rng(seed)
    raw = os.path.join(d, "raw", "gate")
    os.makedirs(os.path.join(raw, "candles"), exist_ok=True)
    os.makedirs(os.path.join(raw, "_raw", "candlesticks_1m", "202610"), exist_ok=True)
    insts = []
    t_form0 = G.GRID0_MS + FIRST * 60_000
    for i in range(n_sym):
        sym, q = "G%d_USDT" % i, quanto_list[i % len(quanto_list)]
        insts.append(dict(exchange="gate", symbol=sym, native_id=sym, contract_size=repr(q)))
        mins = np.sort(rng.choice(NF, size=600, replace=False))
        with gzip.open(os.path.join(raw, "candles", sym + ".csv.gz"), "wt", encoding="utf-8", newline="") as fc, \
                gzip.open(os.path.join(raw, "_raw", "candlesticks_1m", "202610", sym + ".csv.gz"), "wt", encoding="utf-8", newline="") as fa:
            fc.write("t_open_ms,o,h,l,c,v,quote_v\n")
            px = 100.0 + 10 * i
            for m in mins:
                px *= 1 + rng.normal(0, 0.001)
                cl = round(px, 4)
                contracts = int(rng.integers(1, 5000))
                t = t_form0 + int(m) * 60_000
                v_file = contracts if bug_v else contracts * q
                fc.write("%d,%s,%s,%s,%s,%s,\n" % (t, cl, cl, cl, cl, repr(float(v_file)) if not bug_v else contracts))
                fa.write("%d,%d,%s,%s,%s,%s\n" % (t // 1000, contracts, cl, cl, cl, cl))
            # строка за пределами формирования (не должна влиять и читаться как данные)
            tw = G.GRID0_MS + (2479 * 1440 + 5) * 60_000
            fc.write("%d,1,1,1,1,1,\n" % tw)
            fa.write("%d,1,1,1,1,1\n" % (tw // 1000))
    with open(os.path.join(raw, "instruments.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["exchange", "symbol", "native_id", "contract_size"], lineterminator="\n")
        w.writeheader()
        w.writerows(insts)
    return [i["symbol"] for i in insts]


def gate_pairs_for(syms):
    return [dict(pid="aex~gate|X|%s" % s, key=s, cls="crypto", exA="aex", symA="X", exB="gate", symB=s, cell="aex~gate/crypto", legs=(("aex", "X"), ("gate", s)), market="24x7") for s in syms]


@test
def g05_turnover_multiplier_and_gc2_7_gate():
    d = tempfile.mkdtemp()
    syms = write_gate_raw(d, n_sym=14)
    write_manifest(d)
    write_empty_gate_pairs(d)
    cfg_test(d)
    pairs = gate_pairs_for(syms)
    w = W.World(d, test=True, turn_dir=os.path.join(d, "turn"), gate="on")
    assert w.gate_mult() == 1.0
    n = W.build_turnover(w, pairs, G.D_FORM0, G.D_FORM_LAST, os.path.join(d, "raw"))
    assert n == 14, n
    cnt, mx, bad = W.verify_turnover(w, os.path.join(d, "raw"), G.D_FORM0, G.D_FORM_LAST, pairs, n_sym=12, only_ex="gate")
    assert cnt == 12 and mx < 1e-6 and not bad, (cnt, mx, bad)
    mx_ok = mx
    # известный ответ по одному символу, руками: оборот минуты = close × контракты × quanto (из архива)
    sym = syms[1]                                                       # quanto 0.01
    a = np.load(w.turn_path("gate", sym))
    ref = W.gate_reference_turnover(os.path.join(d, "raw"), sym, FIRST, FIRST + NF)
    assert abs(float(a.astype(np.float64).sum()) - float(ref.sum())) / float(ref.sum()) < 1e-6 and (a > 0).sum() == 600
    # красный 1: множитель манифеста 10 (ошибочный) - сверка находит расхождение
    write_manifest(d, mult=10.0)
    w10 = W.World(d, test=True, turn_dir=os.path.join(d, "turn10"), gate="on")
    W.build_turnover(w10, pairs, G.D_FORM0, G.D_FORM_LAST, os.path.join(d, "raw"))
    cnt, mx, bad = W.verify_turnover(w10, os.path.join(d, "raw"), G.D_FORM0, G.D_FORM_LAST, pairs, n_sym=12, only_ex="gate")
    assert cnt == 12 and bad and mx > 1.0, (cnt, mx)
    n_bad10 = len(bad)
    # красный 2: загрузчик не умножил v на quanto (v файла = контракты) при множителе 1 - расхождение у всех контрактов, где quanto != 1
    d2 = tempfile.mkdtemp()
    syms2 = write_gate_raw(d2, n_sym=14, bug_v=True)
    write_manifest(d2)
    write_empty_gate_pairs(d2)
    cfg_test(d2)
    w2 = W.World(d2, test=True, turn_dir=os.path.join(d2, "turn"), gate="on")
    pairs2 = gate_pairs_for(syms2)
    W.build_turnover(w2, pairs2, G.D_FORM0, G.D_FORM_LAST, os.path.join(d2, "raw"))
    cnt, mx, bad = W.verify_turnover(w2, os.path.join(d2, "raw"), G.D_FORM0, G.D_FORM_LAST, pairs2, n_sym=12, only_ex="gate")
    assert cnt == 12 and len(bad) >= 8, (cnt, len(bad))
    print("   G-C2-7 (Gate): 12 символов, макс. расхождение %.2g; красные: множитель 10 -> %d расхождений, v без quanto -> %d" % (mx_ok, n_bad10, len(bad)))


@test
def g06_gate_funding_dir_and_open_label():
    d, _ = make_world()
    add_gate(d)
    cfg_test(d)
    w = W.World(d, test=True)
    p = [p for p in w.universe(check_sha=False) if "gate" in (p["exA"], p["exB"]) and p["key"] == "A0"][0]
    side = 0 if p["exA"] == "gate" else 1
    assert w.fund_path("gate", "G0_USDT") == os.path.join(d, "gate_funding", "G0_USDT.csv.gz")
    assert w.fund_path("aex", "SYM0USDT") == os.path.join(d, "funding", "aex", "SYM0USDT.csv.gz")
    fa = w.fund_array(p, side, G.D_FORM0, G.D_FORM_LAST)
    assert len(fa) == NF and abs(fa.sum() - 0.0015) < 1e-12 and np.count_nonzero(fa) == 5, (fa.sum(), np.count_nonzero(fa))
    assert abs(fa[7 + 480] - 0.0001) < 1e-12 and abs(fa[7 + 5 * 480] - 0.0005) < 1e-12
    assert W.OPEN_SEM["gate"] == "first_trade" and w.gate_state()["info"]["open_label"] == "first_trade"


# ------------------------------------------------------------------------------------------------ таблица пар Gate по R2'
def make_asset_tree(root, k_one=0.01, k_border=0.08, k_diff=0.30, seed=3):
    """Синтетическое дерево F5: 8 бирж (непустые binance, bybit), asset_map.csv, inst/*.csv, inst_gate, gx/asset_gate. Ответ по S известен: lr = -k × (ln c1 - среднее) -> S = k."""
    rng = np.random.default_rng(seed)
    N = CG.N_MIN
    walk = lambda: np.cumsum(rng.normal(0, 1e-3, N)) + np.log(100.0)         # noqa: E731
    os.makedirs(os.path.join(root, "asset"), exist_ok=True)
    os.makedirs(os.path.join(root, "inst"), exist_ok=True)
    os.makedirs(os.path.join(root, "inst_gate"), exist_ok=True)
    gdir = os.path.join(root, "gx", "asset_gate")
    os.makedirs(gdir, exist_ok=True)
    amap_cols = ["exchange", "symbol", "base", "quote", "class", "class_src", "big", "key", "tags", "status", "listed_ms", "delisted_ms", "n_bars", "n_valid", "zero_share", "turnover",
                 "turnover_cv", "price_med", "asset_id", "role", "reason", "main_symbol", "dup_corr", "dup_S", "dup_ratio", "dup_n_ret", "dup_unverified", "leg_c1d2", "a1a2b1"]
    idx_cols = ["row", "file", "n_bars", "n_valid", "qv_rows", "turnover_qv", "turnover_cv", "turnover", "price_med", "first_valid", "last_valid", "err"]
    inst_cols = ["exchange", "symbol", "native_id", "base", "quote", "settle", "class", "class_src", "status", "contract_size", "tick_size", "listed_ms", "delisted_ms", "funding_interval_h",
                 "taker_fee_bps", "maker_fee_bps", "note", "pre_listing", "pre_listing_src"]
    # (биржа, символ, база, класс, ряд ln c, turnover, n_valid_window)
    lw = {k: walk() for k in ("ONE", "BRD", "DIF", "FEW", "EQ", "MIX", "PRE", "COM")}
    spec_other = []
    for k, cls in (("ONE", "crypto"), ("BRD", "crypto"), ("DIF", "crypto"), ("FEW", "crypto"), ("EQ", "equity"), ("MIX", "equity"), ("PRE", "crypto"), ("COM", "crypto")):
        spec_other.append(("binance", k + "USDT", k, cls, lw[k], 1e9, None))
        spec_other.append(("bybit", k + "USDT", k, cls, lw[k] + rng.normal(0, 5e-5, N), 5e8, None))
    gate_spec = [("ONE_USDT", "ONE", "crypto", lw["ONE"] - k_one * (lw["ONE"] - lw["ONE"].mean()) + rng.normal(0, 1e-5, N), 1e8, None, "0", ""),
                 ("BRD_USDT", "BRD", "crypto", lw["BRD"] - k_border * (lw["BRD"] - lw["BRD"].mean()) + rng.normal(0, 1e-5, N), 1e8, None, "0", ""),
                 ("DIF_USDT", "DIF", "crypto", lw["DIF"] - k_diff * (lw["DIF"] - lw["DIF"].mean()) + rng.normal(0, 1e-5, N), 1e8, None, "0", ""),
                 ("FEW_USDT", "FEW", "crypto", lw["FEW"], 1e8, 200, "0", ""),
                 ("EQ_USDT", "EQ", "equity", lw["EQ"] + rng.normal(0, 1e-5, N), 1e8, None, "0", ""),
                 ("MIX_USDT", "MIX", "crypto", lw["MIX"] + rng.normal(0, 1e-5, N), 1e8, None, "0", ""),
                 ("PRE_USDT", "PRE", "crypto", lw["PRE"] + rng.normal(0, 1e-5, N), 1e8, None, "1", "gate is_pre_market=true"),
                 ("COM_USDT", "COM", "commodity", lw["COM"] + rng.normal(0, 1e-5, N), 1e8, None, "0", ""),
                 # дубль внутри Gate: ключ ONE после снятия множителя, тот же ряд ×1000, оборот меньше
                 ("1000ONE_USDT", "1000ONE", "crypto", lw["ONE"] - k_one * (lw["ONE"] - lw["ONE"].mean()) + np.log(1000.0) + rng.normal(0, 1e-5, N), 5e7, None, "0", "")]
    # 1. другие биржи
    by_ex = {ex: [] for ex in CG.EXCH8}
    for ex, sym, base, cls, ln, tv, nv in spec_other:
        by_ex[ex].append((sym, base, cls, ln, tv, nv))
    amap = []
    for ex in CG.EXCH8:
        rows = by_ex[ex]
        arr = np.full((len(rows), N), np.nan, dtype="<f4")
        with open(os.path.join(root, "asset", ex + ".idx.csv"), "w", encoding="utf-8", newline="") as f:
            w = csv.DictWriter(f, fieldnames=idx_cols, lineterminator="\n")
            w.writeheader()
            for r, (sym, base, cls, ln, tv, nv) in enumerate(rows):
                arr[r] = np.exp(ln).astype("<f4")
                w.writerow(dict(row=r, file=sym + ".csv.gz", n_bars=N, n_valid=N, qv_rows=N, turnover_qv=repr(tv), turnover_cv=repr(tv), turnover=repr(tv), price_med="100", first_valid=0, last_valid=N - 1, err=""))
                big = "акции+ETF" if cls == "equity" else "крипта"
                amap.append(dict(exchange=ex, symbol=sym, base=base, quote="USDT", **{"class": cls}, class_src="", big=big, key=base, tags="", status="TRADING", n_bars=N, n_valid=N, zero_share="0.0000",
                                 turnover=repr(tv), turnover_cv=repr(tv), price_med="100", asset_id="%s|%s" % (base, big), role="main", reason="", leg_c1d2=1, a1a2b1=1))
        arr.tofile(os.path.join(root, "asset", ex + ".f32"))
    with open(os.path.join(root, "asset_map.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=amap_cols, lineterminator="\n")
        w.writeheader()
        for r in amap:
            w.writerow({k: r.get(k, "") for k in amap_cols})
    with open(os.path.join(root, "inst", "binance.csv"), "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=inst_cols, lineterminator="\n")
        w.writeheader()
        for r in amap:
            if r["exchange"] == "binance":
                w.writerow(dict(exchange="binance", symbol=r["symbol"], native_id=r["symbol"], base=r["base"], quote="USDT", settle="USDT", **{"class": r["class"]}, class_src="", status="TRADING"))
    open(os.path.join(root, "asset_pairs.csv"), "w", encoding="utf-8").write("key,scope\n")
    # 2. Gate
    arr = np.full((len(gate_spec), N), np.nan, dtype="<f4")
    with open(os.path.join(gdir, "gate.idx.csv"), "w", encoding="utf-8", newline="") as fi, open(os.path.join(root, "inst_gate", "gate.csv"), "w", encoding="utf-8", newline="") as fg:
        wi = csv.DictWriter(fi, fieldnames=idx_cols, lineterminator="\n")
        wi.writeheader()
        wg = csv.DictWriter(fg, fieldnames=inst_cols, lineterminator="\n")
        wg.writeheader()
        for r, (sym, base, cls, ln, tv, nv, pre, pre_src) in enumerate(gate_spec):
            row = np.exp(ln).astype("<f4")
            if nv:
                row[nv:] = np.nan
                row[:0] = np.nan
            arr[r] = row
            nvalid = int(np.isfinite(row).sum())
            wi.writerow(dict(row=r, file=sym + ".csv.gz", n_bars=nvalid, n_valid=nvalid, qv_rows=0, turnover_qv="0.0", turnover_cv=repr(tv), turnover=repr(tv), price_med="100", first_valid=0, last_valid=N - 1, err=""))
            src = {"crypto": "default(contract_type empty)", "equity": "contract_type=stocks", "commodity": "contract_type=commodities"}[cls]
            wg.writerow(dict(exchange="gate", symbol=sym, native_id=sym, base=sym.rsplit("_", 1)[0], quote="USDT", settle="USDT", **{"class": cls}, class_src=src, status="trading", contract_size="1",
                             note="enable_decimal=true (дробный размер ордера)" if base == "FEW" else "", pre_listing=pre, pre_listing_src=pre_src))
    arr.tofile(os.path.join(gdir, "gate.f32"))
    return gdir


@test
def g07_pairs_table_known_answers():
    root = tempfile.mkdtemp()
    gdir = make_asset_tree(root)
    out = tempfile.mkdtemp()
    ns = type("A", (), dict(root=root, asset_gate=gdir, out_dir=out))()
    assert CG.cmd_pairs(ns) == 0
    rows = list(csv.DictReader(open(os.path.join(out, "asset_pairs_gate.csv"), encoding="utf-8")))
    get = lambda s1, s2: [r for r in rows if {r["sym1"], r["sym2"]} == {s1, s2}]               # noqa: E731
    one = get("ONEUSDT", "ONE_USDT")[0]
    assert one["verdict"] == "одно" and one["c1d2"] == "1" and abs(float(one["S"]) - 0.01) < 5e-4 and one["ex1"] == "binance", one         # c1 - нога с большим оборотом (binance)
    brd = get("BRDUSDT", "BRD_USDT")[0]
    assert brd["verdict"].startswith("пограничное") and brd["c1d2"] == "0" and abs(float(brd["S"]) - 0.08) < 5e-4, brd
    dif = get("DIFUSDT", "DIF_USDT")[0]
    assert dif["verdict"] == "разное" and dif["c1d2"] == "0" and abs(float(dif["S"]) - 0.30) < 5e-3, dif
    few = get("FEWUSDT", "FEW_USDT")[0]
    assert few["verdict"].startswith("нет данных") and few["c1d2"] == "0" and int(few["n_ret"]) < 300 and "enable_decimal" in few["gate_note"], few
    eq = get("EQUSDT", "EQ_USDT")[0]
    assert eq["verdict"] == "одно" and eq["c1d2"] == "1" and eq["big1"] == "акции+ETF", eq
    mix = get("MIXUSDT", "MIX_USDT")[0]
    assert mix["class_match"] == "0" and mix["verdict"].startswith("класс не совпал") and mix["c1d2"] == "0", mix
    # пред-рынок и commodity - вне теста: пар нет
    assert not get("PREUSDT", "PRE_USDT") and not get("COMUSDT", "COM_USDT")
    contracts = {r["symbol"]: r for r in csv.DictReader(open(os.path.join(out, "gate_contracts.csv"), encoding="utf-8"))}
    assert contracts["PRE_USDT"]["out_kind"] == "pre_listing" and contracts["COM_USDT"]["out_kind"] == "class_undefined" and contracts["PRE_USDT"]["role"] == "excluded"
    # дубль внутри Gate: 1000ONE_USDT (множитель 1000, тот же ряд) - роль dup, пары с ним c1d2 = 0, основной ONE_USDT - main
    assert contracts["1000ONE_USDT"]["role"] == "dup" and contracts["ONE_USDT"]["role"] == "main" and contracts["1000ONE_USDT"]["leg_c1d2"] == "0"
    dup_pairs = get("ONEUSDT", "1000ONE_USDT")
    assert dup_pairs and all(r["c1d2"] == "0" for r in dup_pairs) and any("дубль" in r["why"] for r in dup_pairs), dup_pairs
    inside = [r for r in rows if r["scope"] == "внутри биржи"]
    assert len(inside) == 1 and inside[0]["c1d2"] == "0"
    meta = json.load(open(os.path.join(out, "asset_pairs_gate.meta.json"), encoding="utf-8"))
    assert meta["pairs_sha256"] == sha(os.path.join(out, "asset_pairs_gate.csv")) and meta["c1d2_pairs_by_class"] == {"крипта": 2, "акции+ETF": 2}, meta["c1d2_pairs_by_class"]
    # запись через World: таблица читается универсумом без правок (после установки рядом с asset_pairs.csv)
    for f in ("asset_pairs_gate.csv", "asset_pairs_gate.meta.json"):
        shutil.copy(os.path.join(out, f), os.path.join(root, f))
    print("   пар Gate %d; c1d2 %s" % (len(rows), meta["c1d2_pairs_by_class"]))


@test
def g08_hints_membership_h4():
    root = tempfile.mkdtemp()
    p = os.path.join(root, "pairs.csv")
    with open(p, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CG.PAIR_COLS + ["gate_note"], lineterminator="\n")
        w.writeheader()
        for ex, sym, c1d2, verdict in (("binance", "TAUSDT", 1, "одно"), ("bingx", "TA-USDT", 1, "одно"), ("aster", "TAUSDT", 0, "нет данных (мало общих доходностей)")):
            w.writerow(dict(key="TA", scope="между биржами", big1="крипта", big2="крипта", class_match=1, ex1=ex, sym1=sym, ex2="gate", sym2="TA_USDT", n_ret=2000, S="0.03", verdict=verdict, c1d2=c1d2))
    out = os.path.join(root, "h.csv")
    ns = type("A", (), dict(pairs=p, out=out, root=root))()
    assert CG.cmd_hints(ns) == 0
    rows = [r for r in csv.DictReader(open(out, encoding="utf-8")) if r["asset"] == "TA"]
    h4 = [r for r in rows if r["ex1"] == "binance"][0]
    assert h4["in_universe_c2"] == "1" and h4["market"] == "24x7" and h4["sym2"] == "TA_USDT"
    assert [r for r in rows if r["ex1"] == "aster"][0]["in_universe_c2"] == "0"
    nobody = [r for r in csv.DictReader(open(out, encoding="utf-8")) if r["asset"] == "BRETT"]
    assert len(nobody) == 1 and "нет контракта" in nobody[0]["verdict"]


# ------------------------------------------------------------------------------------------------ манифест: проверки контракта, окно не читается
def write_gate_contract_files(base, xdl9, sym="G0_USDT", quanto=0.01, wrong_mult=False, panel_wrong=False, fund_wrong=False):
    """Один контракт Gate: свечи, архив, фандинг, панель; строки с метками формирования и «отравленная» строка после формирования (мусор вместо чисел)."""
    t0 = 1_790_812_800_000
    out = os.path.join(base, "gate")
    for sub in ("candles", "funding", "_raw/candlesticks_1m/202610", "_raw/funding_applies/202610"):
        os.makedirs(os.path.join(out, sub), exist_ok=True)
    mins = [0, 1, 2, 5, 8, 100, 20000]
    price = [10.0, 10.5, 10.4, 10.6, 10.7, 11.0, 12.0]
    vol = [100, 200, 300, 400, 500, 600, 700]
    with gzip.open(os.path.join(out, "candles", sym + ".csv.gz"), "wt", encoding="utf-8", newline="") as fc, gzip.open(os.path.join(out, "_raw", "candlesticks_1m", "202610", sym + ".csv.gz"), "wt", encoding="utf-8", newline="") as fa:
        fc.write("t_open_ms,o,h,l,c,v,quote_v\n")
        for m, p, v in zip(mins, price, vol):
            t = t0 + m * 60_000
            vf = v * (quanto * 7 if wrong_mult else quanto)
            fc.write("%d,%s,%s,%s,%s,%s,\n" % (t, p, p, p, p, repr(vf)))
            fa.write("%d,%d,%s,%s,%s,%s\n" % (t // 1000, v, p, p, p, p))
        fc.write("%d,garbage,not,numbers\n" % (t0 + 20160 * 60_000 + 60_000))      # первая минута окна: разбирать нельзя
        fa.write("%d,garbage\n" % ((t0 + 20160 * 60_000 + 60_000) // 1000))
    with gzip.open(os.path.join(out, "funding", sym + ".csv.gz"), "wt", encoding="utf-8", newline="") as f1, gzip.open(os.path.join(out, "_raw", "funding_applies", "202610", sym + ".csv.gz"), "wt", encoding="utf-8", newline="") as f2:
        f1.write("time_ms,rate,interval_h\n")
        for k, r in enumerate((0.0001, 0.0002)):
            t = t0 + (k + 1) * 8 * 3600_000
            f1.write("%d,%s,8.0\n" % (t + 1000 * k, r * (3 if fund_wrong and k else 1)))                    # k = 1: метка +1 с (REST «гуляет»), минута та же
            f2.write("%d,%s\n" % (t // 1000, r))
        f1.write("%d,garbage\n" % (t0 + 20160 * 60_000 + 60_000))
        f2.write("%d,garbage\n" % ((t0 + 20160 * 60_000 + 60_000) // 1000))
    o = np.full(50400, np.nan)
    c = np.full(50400, np.nan)
    for m, p in zip(mins, price):
        o[m] = c[m] = p
    if panel_wrong:
        c[5] += 0.01
    write_sap(os.path.join(xdl9, "panels", "gate", sym + ".sap"), o, c)
    return dict(symbol=sym, native_id=sym, contract_size=repr(quanto), **{"class": "crypto"})


def import_manifest_module():
    sys.path.insert(0, XDL_DIR)
    import gate_manifest as GM
    return GM


@test
def g09_manifest_contract_checks_and_window_not_read():
    GM = import_manifest_module()
    base, xdl9 = tempfile.mkdtemp(), tempfile.mkdtemp()
    row = write_gate_contract_files(base, xdl9)
    r = GM.contract_task((base, xdl9, row))                                  # в файлах отравленная строка окна: мусор не разбирается
    assert r["same_minutes"] and r["n_file"] == 7 and r["n_raw"] == 7 and r["n_pos"] == 7, r
    assert r["max_rel_v"] < 1e-12 and r["turn_rel"] < 1e-12 and r["panel_valid_eq"] and r["panel_max_rel_c"] == 0.0, r
    assert r["fund_n_file"] == r["fund_n_raw"] == 2 and r["fund_rate_eq"] and r["fund_min_eq"] and r["fund_exact_t"] == 1 and r["fund_max_dt_ms"] == 1000, r
    assert r["first_minute_row"] and r["panel_first"] == FIRST and r["open_n_both"] >= 1
    # красные: множитель, значение панели, ставка фандинга
    row2 = write_gate_contract_files(base, xdl9, wrong_mult=True)
    r2 = GM.contract_task((base, xdl9, row2))
    assert r2["max_rel_v"] > 0.8 and r2["turn_rel"] > 0.8, r2
    row3 = write_gate_contract_files(base, xdl9, panel_wrong=True)
    r3 = GM.contract_task((base, xdl9, row3))
    assert r3["panel_max_rel_c"] > 1e-4, r3
    row4 = write_gate_contract_files(base, xdl9, fund_wrong=True)
    r4 = GM.contract_task((base, xdl9, row4))
    assert not r4["fund_rate_eq"], r4
    # граница формирования жёсткая: читатели останавливаются на первой строке позже суток 2478
    assert GM.D1 == 2478 and GM.FORM_END_MS == G.GRID0_MS + 2479 * 1440 * 60_000


def main():
    names = sys.argv[1:]
    fails = 0
    sel = [fn for fn in OK if not names or fn.__name__ in names or any(fn.__name__.startswith(x) for x in names)]
    for fn in sel:
        try:
            fn()
            print("PASS", fn.__name__)
        except (Exception, SystemExit):                      # noqa: BLE001
            fails += 1
            print("FAIL", fn.__name__)
            traceback.print_exc()
    print("итог: %d из %d тестов не прошли" % (fails, len(sel)))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
