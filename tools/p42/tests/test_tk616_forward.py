"""Тесты tk616_forward.py, tk626_freeze.py (TK-616 §13.28 п. 7–8, TK-626 §13.28.1) на синтетике — без движка и без реальных данных: геометрия заморозки, охраны (вселенная точки, деревья, манифест),
префикс, швы, правило решения, сквозной `run_point` с подменным раннером (три вселенные × три клетки), кандидат `L_n24` (TK-638: C1 α из кандидата, C2 охрана таблицы, C3 `replay-n24`), кандидат `L_mk2` (TK-641: цепочка α tk626 → tk638 → tk641, `--no-candidate2`, C3 `replay-mk2`). Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk616_forward.py`.
"""
import argparse
import contextlib
import datetime
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import traceback

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, ".."))
import retitr_full as rf  # noqa: E402
import retitr_full_run as rr  # noqa: E402
import tk616_forward as fw  # noqa: E402
import tk616_freeze as old  # noqa: E402
import tk626_freeze as fz  # noqa: E402

fw.REQUIRE_CANDIDATE = False                          # прежние тесты — заморозка tk626 без кандидата; охрана «нет кандидата — остановка» (TK-638, FIX критика) включается в своих тестах через `strict()`
fw.REQUIRE_CANDIDATE3 = False                         # TK-670: то же для `tk670-frozen.json` (`strict3()` в `test_tk670.py`)
fw.REQUIRE_CANDIDATE2 = False                         # TK-641: то же для `tk641-frozen.json` (`strict2()` в своих тестах)
NB = 4000
DAY = rf.DAY_MS
CRYPTO, TRADFI = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT"], ["EEEUSDT", "FFFUSDT"]
COINS = CRYPTO + TRADFI
NEW1, NEW2 = ("NEWAUSDT", False, "2026-11-20", "2026-11-30"), ("NEWBUSDT", True, "2026-12-05", "2026-12-31")      # прирост L1 / прирост L2 (первые сутки после D1 = 30.11)
BIN_BYTES = b"synthetic-binary"
D = datetime.date.fromisoformat
UNIS = {"all": COINS, "crypto": CRYPTO, "tradfi": TRADFI}


@contextlib.contextmanager
def strict():
    fw.REQUIRE_CANDIDATE = True
    try:
        yield
    finally:
        fw.REQUIRE_CANDIDATE = False


@contextlib.contextmanager
def tmp():
    p = tempfile.mkdtemp(prefix="tk626f-")
    try:
        yield p
    finally:
        shutil.rmtree(p, ignore_errors=True)


def results(passed=("S_mk1",)):
    out = {}
    for c in ("L_mk1", "L_mk2", "S_mk1", "S_mk2"):
        out[c] = {"side": c[0], "sum": 1.0, "n": 100, "mean": 0.01, "p": 0.001 if c in passed else 0.5, "holm_threshold": 0.0125, "holm_reject": c in passed,
                  "positive_months": 4, "counted_months": 5}
    return {"gmk": out}


def gmk_626(smk1=True):
    """Поле `gmk` results.json G-626c: у каждой клетки g1–g3 и итог `pass` (тут S_mk1 — по `smk1`, остальные клетки не влияют)."""
    out = {}
    for c in ("L_mk1", "L_mk2", "S_mk1", "S_mk2"):
        ok = smk1 if c == "S_mk1" else False
        out[c] = {"side": c[0], "sum": 1.0, "n": 100, "mean": 0.01, "p": 0.001, "holm_threshold": 0.0125, "holm_reject": ok, "positive_months": 4, "counted_months": 5,
                  "g1": ok, "g2": ok, "g3": ok, "pass": ok}
    return out


def sha_of(b):
    return hashlib.sha256(b).hexdigest(), hashlib.md5(b).hexdigest()


def write_list(path, coins):
    open(path, "w", encoding="utf-8").write(",".join(coins) + "\n")


def write_universe(root, pid, extra=(), mutate=None):
    """`<root>/<pid>/universe/`: списки all / crypto / tradfi (база + прирост `extra` = [(символ, TradFi?, first_day, last_day)]) и contracts.csv точки."""
    u = os.path.join(root, pid, "universe")
    os.makedirs(u, exist_ok=True)
    lists = {n: list(c) for n, c in UNIS.items()}
    rows = [{"symbol": c, "in_base": 1, "tradfi": int(c in TRADFI), "days_0107_1311": 100, "first_day": "2026-07-01", "last_day": "2026-11-13"} for c in COINS]
    for sym, tf, first, last in extra:
        lists["all"].append(sym)
        lists["tradfi" if tf else "crypto"].append(sym)
        rows.append({"symbol": sym, "in_base": 0, "tradfi": int(tf), "days_0107_1311": 0, "first_day": first, "last_day": last})
    if mutate:
        mutate(lists, rows)
    for n, c in lists.items():
        write_list(os.path.join(u, "symbols-%s.txt" % n), c)
    with open(os.path.join(u, "contracts.csv"), "w", encoding="utf-8", newline="") as f:
        f.write("symbol,in_base,tradfi,days_0107_1311,first_day,last_day\n")
        for r in rows:
            f.write("%(symbol)s,%(in_base)d,%(tradfi)d,%(days_0107_1311)d,%(first_day)s,%(last_day)s\n" % r)
    return u


class Env:
    """Заморозка `tk626-frozen-1`, бинарь, базовые списки 6 монет (4 крипто + 2 TradFi) во временном каталоге."""

    def __init__(self, root, passed=True):
        self.root = root
        self.bin = os.path.join(root, "alpha-mk")
        open(self.bin, "wb").write(BIN_BYTES)
        self.base_dir = os.path.join(root, "base")
        os.makedirs(self.base_dir)
        for n, c in UNIS.items():
            write_list(os.path.join(self.base_dir, "symbols-%s.txt" % n), c)
        open(os.path.join(self.base_dir, "contracts.csv"), "w").write("symbol\n" + "\n".join(COINS) + "\n")
        sha, md5 = sha_of(BIN_BYTES)
        self.old_fz = old.build(results(("S_mk1",)), "r" * 64, self.bin, md5, sha, "s" * 64)
        base = {n: {"sha256": fw.sha256_text(os.path.join(self.base_dir, "symbols-%s.txt" % n)), "n": len(c)} for n, c in UNIS.items()}
        self.fz = fz.build(self.old_fz, "o" * 64, base, fw.sha256_text(os.path.join(self.base_dir, "contracts.csv")), gmk_626(passed), {"ok": True, "files": 24, "diff": []}, "g" * 64, "a" * 64)
        self.freeze = os.path.join(root, "tk626-frozen.json")
        json.dump(self.fz, open(self.freeze, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        self.fwd = os.path.join(root, "fwd")

    def args(self, point, **kw):
        a = dict(freeze=self.freeze, freeze_commit="c0ffee", freeze_sha256=fw.sha256_text(self.freeze), root=self.fwd, bin=self.bin, base_dir=self.base_dir, universe_dir="", threads=1,
                 tick_store="", sig_cache="", funding="", nboot=NB, point=point, go=True)
        a.update(kw)
        return argparse.Namespace(**a)


def expect_guard(fn, needle):
    try:
        fn()
    except rf.GuardError as e:
        assert needle in str(e), "сообщение %r без %r" % (str(e), needle)
        return
    raise AssertionError("ожидалась охрана с %r" % needle)


# ---- заморозка ------------------------------------------------------------------------------------------------------------------

def test_freeze_has_protocol_geometry_alpha_and_registered_vids():
    with tmp() as t:
        e = Env(t)
        f = fw.load_freeze(e.freeze)
        assert f["version"] == "tk626-frozen-1" and f["supersedes"]["commit"] == "96e302480" and f["supersedes"]["sha256"] == "o" * 64
        assert [(w["name"], w["t0"], w["lo"], w["hi"]) for w in f["windows"]] == [
            ("w1", "2026-10-16", "2026-11-17", "2026-12-24"), ("w2", "2026-11-23", "2026-12-25", "2027-01-31"), ("w3", "2026-12-31", "2027-02-01", "2027-03-10"),
            ("w4", "2027-02-07", "2027-03-11", "2027-04-17"), ("w5", "2027-03-17", "2027-04-18", "2027-05-25")]
        assert [(p["id"], p["d"], p["entries_to"], ",".join(p["windows"]), p["decision"]) for p in f["points"]] == [
            ("L1", "2026-11-30", "2026-11-26", "w1", False), ("L2", "2026-12-31", "2026-12-27", "w1,w2", True), ("L3", "2027-01-31", "2027-01-27", "w1,w2", True),
            ("L4", "2027-02-28", "2027-02-24", "w1,w2,w3", True), ("L5", "2027-03-31", "2027-03-27", "w1,w2,w3,w4", True)]
        assert f["alpha"] == {"S_base": 0.03, "S_mk1": 0.01, "L_base": 0.01} and f["point_shares"] == {"L2": 0.04, "L3": 0.10, "L4": 0.20, "L5": 0.66}
        # vid клеток с `t24=12` — те, что записаны в run.json G-626 (движок TK-626, 19.11)
        assert {c: v["vid"] for c, v in f["cells"].items()} == {"S_base": "c8c06a7d70b92", "S_mk1": "cd0838447e287", "L_base": "c3dacae86b422"}
        assert all(v["family"] and "t24=12" in v["keys"].split() for v in f["cells"].values()) and f["cells"]["L_base"]["side"] == "L" and f["cells"]["S_mk1"]["keys"].endswith("mk=1 t24=12")
        assert f["universe"]["read"] == ["all", "crypto", "tradfi"] and f["universe"]["decision"] == "all" and f["universe"]["t24"] == "12"
        assert f["gates"]["G-626a"]["ok"] and f["gates"]["G-626c"]["smk1_passed"] and f["gates"]["G-626c"]["results_sha256"] == "g" * 64
        assert abs(fw.alpha_k(f, "S_base", "L5") - 0.03 * 0.66) < 1e-12 and fw.alpha_k(f, "S_base", "L1") == 0.0 and abs(fw.alpha_k(f, "L_base", "L2") - 0.01 * 0.04) < 1e-12


def test_alpha_when_smk1_fails_g626c_and_other_cells_do_not_change_the_family():
    assert fz.alphas(True) == {"S_base": 0.03, "S_mk1": 0.01, "L_base": 0.01}
    assert fz.alphas(False) == {"S_base": 0.04, "L_base": 0.01}
    assert abs(sum(fz.alphas(False).values()) - 0.05) < 1e-12 and abs(sum(fz.alphas(True).values()) - 0.05) < 1e-12
    with tmp() as t:
        e = Env(t, passed=False)
        f = fw.load_freeze(e.freeze)
        assert f["alpha"] == {"S_base": 0.04, "L_base": 0.01} and list(f["cells"]) == ["S_base", "L_base"] and not f["gates"]["G-626c"]["smk1_passed"]
        base = {n: {"sha256": "b" * 64, "n": 1} for n in fw.UNI_NAMES}
        for cell in ("L_mk1", "L_mk2", "S_mk2"):                                       # прохождение остальных клеток состав F не меняет (§13.28.1 п. 6)
            g = gmk_626(True)
            g[cell].update({"pass": True, "holm_reject": True})
            assert list(fz.build(e.old_fz, "o" * 64, base, "c" * 64, g, {"ok": True})["cells"]) == ["S_base", "S_mk1", "L_base"]
        bad_keys = json.loads(json.dumps(e.old_fz))
        bad_keys["cells"]["S_base"]["keys"] += " e3=1"
        expect_guard(lambda: fz.build(bad_keys, "o" * 64, base, "c" * 64, gmk_626(True), {"ok": True}), "ключи прежней заморозки")


def test_freeze_main_refuses_when_gate_a_failed_and_writes_loadable_file_otherwise():
    with tmp() as t:
        e = Env(t)
        gm, ga, out, oldf = (os.path.join(t, n) for n in ("gmk.json", "gate-a.json", "out.json", "old.json"))
        json.dump({"gmk": gmk_626(True)}, open(gm, "w"))
        json.dump(e.old_fz, open(oldf, "w"))
        args = ["--old", oldf, "--base-dir", e.base_dir, "--gmk", gm, "--gate-a", ga, "--out", out]
        json.dump({"ok": False, "files": 24, "diff": ["wa L_base long-trades.csv"]}, open(ga, "w"))
        expect_guard(lambda: fz.main(args), "G-626a не пройдены")
        assert not os.path.exists(out)
        json.dump({"ok": True, "files": 24, "diff": []}, open(ga, "w"))
        assert fz.main(args) == 0
        f = fw.load_freeze(out)
        assert f["universe"]["base"]["all"] == {"sha256": fw.sha256_text(os.path.join(e.base_dir, "symbols-all.txt")), "n": 6} and f["universe"]["base"]["tradfi"]["n"] == 2
        assert f["gates"]["G-626c"]["results_sha256"] == fw.sha256_text(gm) and f["universe"]["contracts_sha256"] == fw.sha256_text(os.path.join(e.base_dir, "contracts.csv"))
        assert b"\r" not in open(out, "rb").read()                                       # LF: sha256 файла = sha256 блоба в git
        assert old.main([]) == 2                                                          # прежний сборщик заморозки 96e302480 не пишет файл


def test_freeze_sha256_is_the_same_for_lf_and_crlf_copies_of_every_hashed_file():
    """Проверяющий TK-626: заморозка собиралась из копии с autocrlf (CRLF) и не воспроизводилась из блобов git (LF)."""
    CRLF, LF = bytes([13, 10]), bytes([10])

    def crlf_copy(src, dst):
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        open(dst, "wb").write(open(src, "rb").read().replace(CRLF, LF).replace(LF, CRLF))

    with tmp() as t:
        e = Env(t)
        gm, ga, oldf = (os.path.join(t, n) for n in ("gmk.json", "gate-a.json", "old.json"))
        json.dump({"gmk": gmk_626(True)}, open(gm, "w"))
        json.dump({"ok": True, "files": 24, "diff": []}, open(ga, "w"))
        json.dump(e.old_fz, open(oldf, "w"), indent=1)
        c = os.path.join(t, "crlf")
        crlf_base = os.path.join(c, "base")
        for n in os.listdir(e.base_dir):
            crlf_copy(os.path.join(e.base_dir, n), os.path.join(crlf_base, n))
        for n, src in (("gmk.json", gm), ("gate-a.json", ga), ("old.json", oldf)):
            crlf_copy(src, os.path.join(c, n))
        assert CRLF in open(os.path.join(crlf_base, "contracts.csv"), "rb").read() and CRLF in open(os.path.join(c, "old.json"), "rb").read()
        lf_out, crlf_out = os.path.join(t, "lf.json"), os.path.join(c, "out.json")
        assert fz.main(["--old", oldf, "--base-dir", e.base_dir, "--gmk", gm, "--gate-a", ga, "--out", lf_out]) == 0
        assert fz.main(["--old", os.path.join(c, "old.json"), "--base-dir", crlf_base, "--gmk", os.path.join(c, "gmk.json"), "--gate-a", os.path.join(c, "gate-a.json"),
                        "--out", crlf_out]) == 0
        a, b = (json.load(open(p, encoding="utf-8")) for p in (lf_out, crlf_out))
        a["universe"].pop("contracts_files"), b["universe"].pop("contracts_files")      # пути записываются как переданы (--base-dir), содержимое хешей — нет
        assert a == b and fw.sha256_text(lf_out) == hashlib.sha256(open(lf_out, "rb").read()).hexdigest()
        p0 = os.path.join(e.base_dir, "contracts.csv")
        assert fw.sha256_text(p0) == fw.sha256_text(os.path.join(crlf_base, "contracts.csv")) != rf.sha256_file(os.path.join(crlf_base, "contracts.csv"))
        assert json.load(open(lf_out, encoding="utf-8"))["universe"]["contracts_files"]["srv"] == fz.SRV_CONTRACTS


def test_load_freeze_rejects_broken_geometry():
    def broken(mut):
        with tmp() as t:
            e = Env(t)
            f = json.loads(json.dumps(e.fz))
            mut(f)
            json.dump(f, open(e.freeze, "w", encoding="utf-8"))
            fw.load_freeze(e.freeze)

    expect_guard(lambda: fw.load_freeze("/nonexistent/tk626-frozen.json"), "нет файла заморозки")
    expect_guard(lambda: broken(lambda f: f.update(version="tk616-frozen-1")), "заменена §13.28.1")
    expect_guard(lambda: broken(lambda f: f["windows"][1].update(t0="2026-11-24")), "T0")
    expect_guard(lambda: broken(lambda f: f["windows"][0].update(lo="2026-11-18")), "зачёт")
    expect_guard(lambda: broken(lambda f: f["alpha"].update(S_base=0.06)), "α")
    expect_guard(lambda: broken(lambda f: f["alpha"].pop("S_mk1")), "α заданы не для всех")
    expect_guard(lambda: broken(lambda f: f["point_shares"].update(L5=0.5)), "доли α")
    expect_guard(lambda: broken(lambda f: f["points"][1].update(entries_to="2026-12-28")), "входы до")
    expect_guard(lambda: broken(lambda f: f["points"][2].update(windows=["w1"])), "окна")
    expect_guard(lambda: broken(lambda f: f["points"][0].update(decision=True)), "признак решения")
    expect_guard(lambda: broken(lambda f: f["cells"]["S_base"].update(family=False)), "S_base")
    expect_guard(lambda: broken(lambda f: f["cells"]["L_base"].update(keys=f["cells"]["L_base"]["keys"].replace(" t24=12", ""))), "t24=12")
    expect_guard(lambda: broken(lambda f: f["universe"].update(read=["all", "crypto"])), "вселенные чтения")
    expect_guard(lambda: broken(lambda f: f["universe"].update(decision="other")), "вселенные чтения")
    expect_guard(lambda: broken(lambda f: f["universe"]["base"]["tradfi"].update(sha256="x")), "sha256 базового списка")
    expect_guard(lambda: broken(lambda f: f["universe"].pop("contracts_sha256")), "contracts.csv")
    expect_guard(lambda: broken(lambda f: f.pop("universe")), "нет поля")
    expect_guard(lambda: broken(lambda f: f.pop("windows")), "нет поля")


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *a], capture_output=True, text=True)


def test_freeze_commit_guard_with_git_and_without():
    with tmp() as t:
        e = Env(t)
        if git(t, "init", "-q").returncode != 0:
            return                                                             # git недоступен: охрана без git проверяется ниже
        expect_guard(lambda: fw.check_freeze_committed(e.freeze, t), "не закоммичена")
        git(t, "add", "tk626-frozen.json")
        expect_guard(lambda: fw.check_freeze_committed(e.freeze, t), "не закоммичена")           # в индексе — ещё не коммит
        assert git(t, "commit", "-q", "-m", "freeze").returncode == 0
        commit, sha = fw.check_freeze_committed(e.freeze, t)
        assert len(commit) == 40 and sha == fw.sha256_text(e.freeze)
        open(e.freeze, "a").write("\n ")
        expect_guard(lambda: fw.check_freeze_committed(e.freeze, t), "изменена после коммита")
    with tmp() as t:
        e = Env(t)
        expect_guard(lambda: fw.check_freeze_committed(e.freeze, t), "нужны --freeze-commit и --freeze-sha256")
        expect_guard(lambda: fw.check_freeze_committed(e.freeze, t, "abc", "0" * 64), "≠ заданному")
        assert fw.check_freeze_committed(e.freeze, t, "abc", fw.sha256_text(e.freeze)) == ("abc", fw.sha256_text(e.freeze))
        expect_guard(lambda: fw.check_freeze_committed(os.path.join(t, "нет.json"), t, "abc", "x"), "нет файла")


def test_binary_other_than_frozen_is_refused():
    with tmp() as t:
        e = Env(t)
        f = fw.load_freeze(e.freeze)
        sha, md5 = sha_of(BIN_BYTES)
        assert fw.check_binary(e.bin, f) == (md5, sha)
        other = os.path.join(t, "other")
        open(other, "wb").write(BIN_BYTES + b"x")
        expect_guard(lambda: fw.check_binary(other, f), "≠ замороженным")
        expect_guard(lambda: fw.check_binary("", f), "--bin")
        expect_guard(lambda: fw.check_binary(os.path.join(t, "нет"), f), "--bin")


def test_point_is_readable_only_after_its_last_day_ends_utc():
    with tmp() as t:
        f = fw.load_freeze(Env(t).freeze)
        p = fw.get_point(f, "L2")
        expect_guard(lambda: fw.check_point_date(p, D("2026-12-30")), "ещё не закончилась")
        expect_guard(lambda: fw.check_point_date(p, D("2026-12-31")), "ещё не закончилась")         # сутки D идут — читать нельзя
        fw.check_point_date(p, D("2027-01-01"))
        expect_guard(lambda: fw.get_point(f, "L9"), "не из заморозки")


def mk_tree(root, pid, wins, last_day, coins=None, late=None, first=None):
    """Деревья окон точки: файл `…-YYYY-MM-DD.zip` на каждые сутки от T0 (у прироста — от первых суток) до min(D, T0 + 73), для aggtrades и klines."""
    first = first or {}
    for w in wins:
        end = min(last_day, w.t0d + datetime.timedelta(days=rf.TRADE_END))
        for sub in ("aggtrades", "klines"):
            for c in (coins or COINS):
                p = os.path.join(root, pid, w.name, sub, c)
                os.makedirs(p, exist_ok=True)
                day = max(w.t0d, D(first[c])) if c in first else w.t0d
                while day <= end:
                    open(os.path.join(p, "%s-%s-%s.zip" % (c, sub, day)), "w").close()
                    day += datetime.timedelta(days=1)
                if late:
                    open(os.path.join(p, "%s-%s-%s.zip" % (c, sub, late)), "w").close()


def test_tree_with_a_day_after_the_point_date_or_a_foreign_coin_is_refused():
    with tmp() as t:
        f = fw.load_freeze(Env(t).freeze)
        wins = {w.name: w for w in fw.windows_of(f)}
        used = [wins["w1"], wins["w2"]]
        expect_guard(lambda: fw.check_tree_days(os.path.join(t, "L2"), used, D("2026-12-31")), "нет дерева данных окна")
        mk_tree(t, "L2", used, D("2026-12-31"), coins=COINS[:1])
        n = 2 * ((D("2026-12-28") - D("2026-10-16")).days + 1 + (D("2026-12-31") - D("2026-11-23")).days + 1)      # две вида × (w1: до T0 + 73 = 28.12; w2: до D = 31.12)
        assert fw.check_tree_days(os.path.join(t, "L2"), used, D("2026-12-31")) == n
        assert fw.check_tree_days(os.path.join(t, "L2"), used, D("2026-12-31"), allowed={COINS[0]}) == n
        expect_guard(lambda: fw.check_tree_days(os.path.join(t, "L2"), used, D("2026-12-31"), allowed={"ZZZUSDT"}), "вне списка all")
        shutil.rmtree(os.path.join(t, "L2"))
        mk_tree(t, "L2", used, D("2026-12-31"), coins=COINS[:1], late="2027-01-01")
        expect_guard(lambda: fw.check_tree_days(os.path.join(t, "L2"), used, D("2026-12-31")), "позже 2026-12-31")


def test_read_marker_is_atomic_and_second_read_is_refused():
    with tmp() as t:
        p = fw.claim_read(t, "L2", {"point": "L2"})
        assert os.path.basename(p) == "READ-L2.started" and json.load(open(p))["point"] == "L2"
        expect_guard(lambda: fw.claim_read(t, "L2", {}), "повторное чтение запрещено")
        fw.claim_read(t, "L3", {})


# ---- вселенная точки ----------------------------------------------------------------------------------------------------------------

def test_universe_of_a_point_is_base_plus_listing_increment_and_guards_every_rule():
    pt = {"id": "L1", "d": "2026-11-30"}
    with tmp() as t:
        e = Env(t)
        f = fw.load_freeze(e.freeze)
        u = write_universe(t, "L1", [NEW1])
        r = fw.check_universe(f, u, e.base_dir, pt)
        assert r["lists"]["all"] == COINS + ["NEWAUSDT"] and r["lists"]["crypto"] == CRYPTO + ["NEWAUSDT"] and r["lists"]["tradfi"] == TRADFI
        assert set(r["sha256"]) == set(fw.UNI_NAMES) and len(r["contracts_sha256"]) == 64 and r["text"]["all"] == ",".join(COINS + ["NEWAUSDT"])
        u2 = write_universe(t, "L2", [NEW1, NEW2])
        r2 = fw.check_universe(f, u2, e.base_dir, {"id": "L2", "d": "2026-12-31"}, r["lists"])
        assert r2["lists"]["tradfi"] == TRADFI + ["NEWBUSDT"] and r2["lists"]["all"] == COINS + ["NEWAUSDT", "NEWBUSDT"]

    def bad(needle, extra=(), mutate=None, prev=None, base_edit=None, rm=None):
        with tmp() as t:
            e = Env(t)
            f = fw.load_freeze(e.freeze)
            if base_edit:
                base_edit(e.base_dir)
            u = write_universe(t, pt["id"], extra, mutate)
            if rm:
                os.remove(os.path.join(u, rm))
            expect_guard(lambda: fw.check_universe(f, u, e.base_dir, pt, prev), needle)

    bad("sha256", [NEW1], base_edit=lambda b: write_list(os.path.join(b, "symbols-tradfi.txt"), TRADFI[:1]))                  # базовый список не тот, что в заморозке
    bad("нет базового списка", [NEW1], base_edit=lambda b: os.remove(os.path.join(b, "symbols-all.txt")))
    bad("нет списка вселенной точки", [NEW1], rm="symbols-crypto.txt")
    bad("нет", [NEW1], rm="contracts.csv")
    bad("не содержит базовых монет", [NEW1], mutate=lambda l, r: [l["all"].remove("AAAUSDT"), l["crypto"].remove("AAAUSDT"), r.pop(0)])
    bad("повторы монет", [NEW1], mutate=lambda l, r: l["crypto"].append("AAAUSDT"))
    bad("all ≠ crypto ⊔ tradfi", [NEW1], mutate=lambda l, r: l["tradfi"].append("NEWAUSDT"))                                     # монета в двух классах
    bad("all ≠ crypto ⊔ tradfi", [NEW1], mutate=lambda l, r: l["crypto"].remove("NEWAUSDT"))                                      # монета без класса
    bad("не совпадают по монетам", [NEW1], mutate=lambda l, r: r.pop())
    bad("расходятся со списками", [NEW1], mutate=lambda l, r: r[0].update(tradfi=1))                                              # класс в contracts.csv ≠ списку
    bad("расходятся со списками", [NEW1], mutate=lambda l, r: r[-1].update(in_base=1))
    bad("days_0107_1311 = 0", [NEW1], mutate=lambda l, r: r[0].update(days_0107_1311=0))                                          # у базового контракта есть сутки 01.07–13.11
    bad("days_0107_1311 = 3", [NEW1], mutate=lambda l, r: r[-1].update(days_0107_1311=3))                                         # у прироста в 01.07–13.11 суток быть не может
    bad("вне (2026-11-13; 2026-11-30]", [("NEWAUSDT", False, "2026-11-13", "2026-11-30")])                                       # первые сутки не позже 13.11
    bad("вне (2026-11-13; 2026-11-30]", [("NEWAUSDT", False, "2026-11-20", "2026-12-01")])                                       # последние сутки позже D
    bad("вне (2026-11-13; 2026-11-30]", [("NEWAUSDT", False, "2026-11-25", "2026-11-24")])                                       # last < first
    bad("вне (2026-11-13; 2026-11-30]", [NEW2])                                                                                  # первые сутки после D
    bad("не содержит вселенной предыдущей точки", [NEW1], prev={"all": COINS + ["GONEUSDT"], "crypto": CRYPTO + ["GONEUSDT"], "tradfi": TRADFI})
    bad("не содержит вселенной предыдущей точки", [NEW1], prev={"all": COINS + ["NEWAUSDT"], "crypto": CRYPTO, "tradfi": TRADFI + ["NEWAUSDT"]})   # класс прироста сменился


def test_manifest_records_linked_files_and_guards_changes_between_points():
    with tmp() as t:
        f = fw.load_freeze(Env(t).freeze)
        wins = {w.name: w for w in fw.windows_of(f)}
        used = [wins["w1"], wins["w2"]]
        mk_tree(t, "L2", used, D("2026-12-31"), coins=COINS[:2])
        real = os.path.join(t, "real.bin")
        open(real, "wb").write(b"payload")
        link = os.path.join(t, "L2", "w1", "aggtrades", COINS[0], "%s-aggtrades-2026-11-05.zip" % COINS[0])
        os.remove(link)
        try:
            os.symlink(real, link)
        except (OSError, NotImplementedError):
            shutil.copy(real, link)                                                   # без права на ссылки (Windows): файл с теми же байтами
        man = fw.tree_manifest(os.path.join(t, "L2"), used)
        k = ("aggtrades", COINS[0], "2026-11-05")
        assert man[k] == (hashlib.sha256(b"payload").hexdigest(), 7) and man[("klines", COINS[0], "2026-11-05")][1] == 0
        mp = os.path.join(t, "files.tsv")
        fw.write_manifest(mp, man)
        assert fw.read_manifest(mp) == man and b"\r" not in open(mp, "rb").read()
        assert fw.manifest_guard(man, dict(man)) == []
        more = dict(man)
        more[("klines", COINS[0], "2027-01-30")] = ("0" * 64, 1)                      # новые сутки — допустимы
        assert fw.manifest_guard(man, more) == []
        changed = dict(man)
        changed[k] = ("1" * 64, 7)
        assert fw.manifest_guard(man, changed)[0].startswith("изменён файл")
        gone = dict(man)
        gone.pop(k)
        assert fw.manifest_guard(man, gone)[0].startswith("пропал файл")
        other = os.path.join(t, "L2", "w2", "aggtrades", COINS[0], "%s-aggtrades-2026-11-25.zip" % COINS[0])    # одни сутки в двух окнах — разные файлы: стоп
        w1f = os.path.join(t, "L2", "w1", "aggtrades", COINS[0], "%s-aggtrades-2026-11-25.zip" % COINS[0])
        open(w1f, "wb").write(b"a")
        open(other, "wb").write(b"b")
        expect_guard(lambda: fw.tree_manifest(os.path.join(t, "L2"), used), "окна точки расходятся по файлу")


# ---- таблицы: префикс, швы, конец данных -----------------------------------------------------------------------------------------

def row(sym, day, hour, hold_h=2, gross=1.0, fees=0.3, kind="tp"):
    ent = int(datetime.datetime(day.year, day.month, day.day, tzinfo=datetime.timezone.utc).timestamp() * 1000) + hour * 3_600_000
    return (sym, ent, ent + hold_h * 3_600_000, gross, fees, 0.0, kind)


def test_prefix_guard_equal_extra_missing_different_and_forced():
    to = D("2026-12-27")
    a = [row("AAAUSDT", D("2026-12-01"), 3), row("BBBUSDT", D("2026-12-10"), 5), row("AAAUSDT", D("2026-12-26"), 20, kind="forced", hold_h=100),
         row("CCCUSDT", D("2026-12-29"), 2)]                                                       # последняя — позже D − 4: в сравнение не входит
    same = [r for r in a]
    diffs, forced = fw.prefix_guard({"S_base": a}, {"S_base": same}, to)
    assert diffs["S_base"] == [] and forced["S_base"] == 1
    cur = [a[0], a[1], row("AAAUSDT", D("2026-12-26"), 20, hold_h=10), row("CCCUSDT", D("2026-12-29"), 2, gross=9.0)]      # forced закрыта по-другому: допустимо; хвост — другой: не сверяется
    assert fw.prefix_guard({"S_base": a}, {"S_base": cur}, to)[0]["S_base"] == []
    extra = cur + [row("DDDUSDT", D("2026-12-20"), 1)]
    assert [x.startswith("только в точке k + 1") for x in fw.prefix_guard({"S_base": a}, {"S_base": extra}, to)[0]["S_base"]] == [True]
    assert [x.startswith("только в точке k:") for x in fw.prefix_guard({"S_base": a}, {"S_base": cur[1:]}, to)[0]["S_base"]] == [True]
    moved = [row("AAAUSDT", D("2026-12-01"), 3, hold_h=3), a[1], a[2]]
    assert [x.startswith("различие") for x in fw.prefix_guard({"S_base": a}, {"S_base": moved}, to)[0]["S_base"]] == [True]
    gross = [a[0], row("BBBUSDT", D("2026-12-10"), 5, gross=1.5), a[2]]
    assert len(fw.prefix_guard({"S_base": a}, {"S_base": gross}, to)[0]["S_base"]) == 1
    fees = [a[0], row("BBBUSDT", D("2026-12-10"), 5, fees=0.31), a[2]]
    assert len(fw.prefix_guard({"S_base": a}, {"S_base": fees}, to)[0]["S_base"]) == 1


def trade_rows(w, items):
    """Таблица окна из абсолютных строк (в мс окна от T0)."""
    off = fw.t0_ms(w.t0d)
    return rf.table_from_rows([{"sym": r[0], "entry_ms": r[1] - off, "exit_ms": r[2] - off, "gross": r[3], "fees": r[4], "funding": r[5], "net": r[3] - r[4], "kind": r[6]}
                               for r in items])


def test_seam_check_g3_compares_the_overlap_piece_only():
    w1, w2 = rf.Window("w1", "2026-10-16", "2026-11-17", "2026-12-24"), rf.Window("w2", "2026-11-23", "2026-12-25", "2027-01-31")
    lo, hi = D("2026-12-25"), D("2026-12-27")                                                        # кусок перекрытия: T0(w2) + 32 … T0(w1) + 72
    assert [p[2:] for p in fw.seam_pairs([w1, w2])] == [(lo, hi)]
    inside = [row("AAAUSDT", D("2026-12-25"), 4), row("BBBUSDT", D("2026-12-27"), 22, hold_h=1)]
    outside = [row("CCCUSDT", D("2026-12-20"), 4)]                                                    # до куска: разгон w2 — не сверяется
    ta, tb = trade_rows(w1, inside + outside), trade_rows(w2, inside + [row("CCCUSDT", D("2026-12-20"), 4, gross=7.0)])
    assert fw.seams_check({"w1": ta, "w2": tb}, [w1, w2], D("2026-12-27")) == []
    tb2 = trade_rows(w2, inside[:1])
    d1 = fw.seams_check({"w1": ta, "w2": tb2}, [w1, w2], D("2026-12-27"))
    assert len(d1) == 1 and d1[0].startswith("w1–w2: только в w1")
    tb3 = trade_rows(w2, [inside[0], row("BBBUSDT", D("2026-12-27"), 22, hold_h=1, gross=4.0)])
    assert fw.seams_check({"w1": ta, "w2": tb3}, [w1, w2], D("2026-12-27"))[0].startswith("w1–w2: различие gross/fees")
    assert fw.seams_check({"w1": ta, "w2": tb2}, [w1, w2], D("2026-12-24")) == []                    # кусок целиком позже входов ≤ D − 4: нет сравнения
    assert fw.seams_check({"w1": ta}, [w1], D("2026-12-27")) == []                                    # одно окно — швов нет


def test_end_of_data_check_rejects_trades_past_the_last_day():
    w = rf.Window("w1", "2026-10-16", "2026-11-17", "2026-12-24")
    end = fw.t0_ms(D("2026-11-30")) + DAY
    ok = [row("AAAUSDT", D("2026-11-30"), 20, hold_h=4)]                                              # выход ровно на конце суток D — допустим
    fw.end_of_data_check(ok, w, D("2026-11-30"))
    expect_guard(lambda: fw.end_of_data_check([row("AAAUSDT", D("2026-11-30"), 20, hold_h=5)], w, D("2026-11-30")), "позже конца данных")
    expect_guard(lambda: fw.end_of_data_check([row("AAAUSDT", D("2026-12-01"), 0, hold_h=1)], w, D("2026-11-30")), "позже конца данных")
    assert ok[0][2] == end
    w_early = rf.Window("w1", "2026-10-16", "2026-11-17", "2026-12-24")
    fw.end_of_data_check([row("AAAUSDT", D("2026-12-28"), 20, hold_h=4)], w_early, D("2027-02-01"))        # данные окна кончаются T0 + 73 = 28.12, не D
    expect_guard(lambda: fw.end_of_data_check([row("AAAUSDT", D("2026-12-29"), 1)], w_early, D("2027-02-01")), "позже конца данных")


# ---- статистика и правило ----------------------------------------------------------------------------------------------------------------

def st(**kw):
    s = dict(n=80, sum=40.0, mean=0.5, p=0.0001, alpha_k=0.0012, mean_wo_days=0.4, mean_wo_coins=0.4, fund_ok=True, worst_days=0.2, worst_coins=0.2)
    s.update(kw)
    return s


def test_decide_follows_the_rule_verbatim():
    pt = {"id": "L2"}
    assert fw.decide(pt, False, st()) == "подтверждено"
    assert fw.decide(pt, False, st(alpha_k=0.0)) == "описательно"
    for bad in (dict(p=0.0013), dict(sum=-1.0), dict(mean_wo_days=0.0), dict(mean_wo_coins=-0.1), dict(fund_ok=False), dict(n=59)):
        assert fw.decide(pt, False, st(**bad)) == "не ясно", bad
    assert fw.decide(pt, False, st(n=60)) == "подтверждено" and fw.decide(pt, False, st(p=0.0012)) == "подтверждено"             # границы включительно
    neg = dict(mean=-0.2, sum=-10.0, worst_days=-0.1, worst_coins=-0.05, p=0.9, mean_wo_days=-0.3, mean_wo_coins=-0.3)
    assert fw.decide(pt, False, st(**neg)) == "не ясно"                                                                         # опровергнуто — только на последней
    assert fw.decide({"id": "L5"}, True, st(**neg)) == "опровергнуто"
    assert fw.decide({"id": "L5"}, True, st(**dict(neg, worst_days=0.0))) == "не ясно"
    assert fw.decide({"id": "L5"}, True, st(**dict(neg, worst_coins=0.01))) == "не ясно"
    assert fw.decide({"id": "L5"}, True, st(**dict(neg, mean=0.0))) == "не ясно"


def synth_rows(mean, n_per_day=3, days=41, sd=0.4, seed=1):
    rng = np.random.default_rng(seed)
    out = []
    for i in range(days):
        day = fw.F0 + datetime.timedelta(days=i)
        for j in range(n_per_day):
            out.append(row(COINS[(i + j) % len(COINS)], day, 2 + j, gross=float(mean + 0.3 + rng.normal(0, sd)), fees=0.3))
    return out


def test_cell_point_stats_confirms_a_strong_series_and_stays_descriptive_before_l2():
    with tmp() as t:
        f = fw.load_freeze(Env(t).freeze)
    to = D("2026-12-27")
    s = fw.cell_point_stats(f, "S_base", fw.get_point(f, "L2"), synth_rows(0.8), to, None, NB)
    assert s["n"] == 123 and s["days"] == 41 and s["decision"] == "подтверждено" and s["p"] <= s["alpha_k"] == 0.03 * 0.04 and s["fund_ok"] and s["fund_mode"] == "модуль фандинга движка"
    assert abs(s["sum"] - 123 * s["mean"]) < 1e-9
    z = fw.cell_point_stats(f, "S_base", fw.get_point(f, "L2"), synth_rows(0.0, sd=0.2), to, None, NB)
    assert z["decision"] == "не ясно" and z["p"] > z["alpha_k"]
    one = fw.get_point(f, "L1")
    d1 = fw.cell_point_stats(f, "S_base", one, synth_rows(0.8, days=10), D("2026-11-26"), None, NB)
    assert d1["alpha_k"] == 0.0 and d1["decision"] == "описательно"
    l = fw.cell_point_stats(f, "L_base", fw.get_point(f, "L2"), synth_rows(0.8), to, None, NB)
    assert l["alpha_k"] == 0.01 * 0.04 and l["decision"] == "подтверждено"                       # L_base — решающий член (§13.28.1 п. 3)
    for u in ("crypto", "tradfi"):                                                              # остальные вселенные — описательно, α = 0 при любой силе ряда
        c = fw.cell_point_stats(f, "S_base", fw.get_point(f, "L2"), synth_rows(0.8), to, None, NB, uni=u)
        assert c["uni"] == u and c["alpha_k"] == 0.0 and c["decision"] == "описательно" and c["p"] <= 0.03 * 0.04
    assert fw.cell_point_stats(f, "S_base", fw.get_point(f, "L2"), synth_rows(0.8), to, None, NB)["uni"] == "all"
    e = fw.cell_point_stats(f, "S_base", fw.get_point(f, "L2"), [], to, None, NB)
    assert e["n"] == 0 and e["decision"] == "не ясно"
    five = fw.cell_point_stats(f, "S_base", fw.get_point(f, "L5"), synth_rows(-0.8, days=130), D("2027-03-27"), None, NB)
    assert five["decision"] == "опровергнуто" and five["mean"] < 0


def test_without_two_best_days_or_coins_reads_the_concentrated_win_as_not_clear():
    rows = synth_rows(0.02, sd=0.05)
    rows[0] = row(rows[0][0], fw.F0, 2, gross=500.0)                                   # одни сутки и одна монета держат всю сумму
    with tmp() as t:
        f = fw.load_freeze(Env(t).freeze)
    s = fw.cell_point_stats(f, "S_base", fw.get_point(f, "L2"), rows, D("2026-12-27"), None, NB)
    assert s["sum"] > 0 and s["mean_wo_days"] < s["mean"] and s["decision"] in ("подтверждено", "не ясно")
    S = np.array([100.0, 1.0, 1.0, -1.0]); N = np.array([2.0, 1.0, 1.0, 1.0])
    assert fw.mean_without(S, N, 2) == 0.0 and abs(fw.mean_without(S, N, 1, best=False) - 102.0 / 4) < 1e-12 and np.isnan(fw.mean_without(S, N, 4))


# ---- сквозной запуск точки с подменным раннером --------------------------------------------------------------------------------------

def h(*parts):
    return int(hashlib.md5("|".join(map(str, parts)).encode()).hexdigest()[:12], 16)


KEYS = ["%s|%s" % (c, u) for c in ("S_base", "S_mk1", "L_base") for u in fw.UNI_NAMES]               # девять пар «клетка × вселенная»
FIRST = {NEW1[0]: NEW1[2], NEW2[0]: NEW2[2]}                                                      # первые сутки прироста (мало данных до них нет)


class FakeRunner:
    """Вместо движка: из строк `--variants` пишет `<out>/<vid>/{long,short}-trades.csv` для монет из `--symbols`; сделка — функция (вариант, монета, сутки), поэтому окна и вселенные согласованы."""

    def __init__(self, last_day, gross_shift=0.0):
        self.last_day, self.calls, self.gross_shift, self.symbols = last_day, 0, gross_shift, []

    def run(self, cmd, env=None, log=None):
        self.calls += 1
        vfile, out, t0, syms = cmd[cmd.index("--variants") + 1], cmd[cmd.index("--out") + 1], env["P42_T0"], cmd[cmd.index("--symbols") + 1].split(",")
        self.symbols.append(syms)
        for line in open(vfile, encoding="utf-8"):
            vid, keys = line.rstrip("\n").split(" ", 1)
            side = "short" if "bm=1" in keys else "long"
            os.makedirs(os.path.join(out, vid), exist_ok=True)
            with open(os.path.join(out, vid, side + "-trades.csv"), "w", encoding="utf-8", newline="") as f:
                f.write("sym,entry_ms,exit_ms,gross,fees,funding,net,kind\n")
                for r in self.trades(vid, side, D(t0), syms):
                    f.write("%s,%d,%d,%r,%r,0.0,%r,%s\n" % (r[0], r[1], r[2], r[3], r[4], r[3] - r[4], r[6]))
        return 0

    def trades(self, vid, side, t0d, syms):
        t0ms = fw.t0_ms(t0d)
        lim = fw.t0_ms(min(self.last_day, t0d + datetime.timedelta(days=rf.TRADE_END))) + DAY
        out = []
        for k in range(rf.GRID_START_DAY, rf.TRADE_END + 1):
            day = t0d + datetime.timedelta(days=k)
            if day > self.last_day:
                break
            for c in syms:
                if c in FIRST and day < D(FIRST[c]):
                    continue
                x = h(vid, c, day)
                if x % 2:
                    continue
                ent = fw.t0_ms(day) + (x >> 4) % 20 * 3_600_000
                ext, kind = ent + (1 + (x >> 9) % 19) * 3_600_000, "tp"
                if ext > lim:
                    ext, kind = lim, "forced"
                gross = 1.0 + (x >> 14) % 100 / 50.0 + (self.gross_shift if side == "long" else 0.8)
                out.append((c, ent - t0ms, ext - t0ms, gross, 0.3, 0.0, kind))
        return out


EXTRA = {"L1": [NEW1], "L2": [NEW1, NEW2]}


def prepare(e, pid, last_day, extra=None, mutate=None):
    """Вселенная точки (`<fwd>/<pid>/universe`) и деревья её окон на все монеты вселенной."""
    f = fw.load_freeze(e.freeze)
    wins = {w.name: w for w in fw.windows_of(f)}
    extra = EXTRA[pid] if extra is None else extra
    write_universe(e.fwd, pid, extra, mutate)
    mk_tree(e.fwd, pid, [wins[n] for n in fw.get_point(f, pid)["windows"]], last_day, coins=COINS + [x[0] for x in extra], first=FIRST)


def run(e, pid, today, last_day, runner=None, **kw):
    if not os.path.isdir(os.path.join(e.fwd, pid)):
        prepare(e, pid, last_day)
    r = runner or FakeRunner(last_day)
    rc = fw.run_point(e.args(pid, **kw), runner=r, today=today, repo=e.root)
    return rc, r


def test_run_point_l1_then_l2_writes_tables_marker_and_checks_the_prefix():
    with tmp() as t:
        e = Env(t)
        rc, r = run(e, "L1", D("2026-12-01"), D("2026-11-30"))
        assert rc == 0 and r.calls == 3                                                      # одно окно w1 × три вселенные = три вызова движка (клетки одним пакетом)
        assert [sorted(s) for s in r.symbols] == [sorted(COINS + ["NEWAUSDT"]), sorted(CRYPTO + ["NEWAUSDT"]), sorted(TRADFI)]
        marker = json.load(open(os.path.join(e.fwd, "READ-L1.started"), encoding="utf-8"))
        assert os.path.exists(os.path.join(e.fwd, "READ-L1.done")) and marker["universe_n"] == {"all": 7, "crypto": 5, "tradfi": 2} and set(marker["universe_sha256"]) == set(fw.UNI_NAMES)
        assert len(marker["contracts_sha256"]) == 64 and marker["tree_files"] > 0 and marker["freeze_commit"] == "c0ffee"
        p1 = json.load(open(os.path.join(e.fwd, "point-L1.json"), encoding="utf-8"))
        assert p1["prefix"]["diffs"] == {} and all(p1["stats"][k]["decision"] == "описательно" and p1["stats"][k]["alpha_k"] == 0.0 for k in KEYS)
        assert p1["stats"]["S_base|all"]["n"] > 0 and p1["seams"] == {k: 0 for k in KEYS} and p1["manifest_files"] > 0
        tabs = json.load(open(os.path.join(e.fwd, "tables-L1.json"), encoding="utf-8"))
        assert tabs["entries_to"] == "2026-11-26" and set(tabs["rows"]) == set(KEYS) and tabs["universe"]["tradfi"] == TRADFI
        assert all(fw.date_of(x[1]) <= D("2026-11-26") for rows in tabs["rows"].values() for x in rows)
        assert {x[0] for x in tabs["rows"]["S_base|tradfi"]} <= set(TRADFI) and {x[0] for x in tabs["rows"]["L_base|crypto"]} <= set(CRYPTO + ["NEWAUSDT"])
        assert os.path.exists(os.path.join(e.fwd, "files-L1.tsv"))
        rc2, r2 = run(e, "L2", D("2027-01-01"), D("2026-12-31"))
        assert rc2 == 0 and r2.calls == 6                                                    # w1 + w2, три вселенные
        p2 = json.load(open(os.path.join(e.fwd, "point-L2.json"), encoding="utf-8"))
        assert p2["prefix"]["diffs"] == {k: 0 for k in KEYS} and p2["seams"] == {k: 0 for k in KEYS} and p2["universe"]["n"] == {"all": 8, "crypto": 5, "tradfi": 3}
        assert p2["stats"]["S_base|all"]["decision"] == "подтверждено" and p2["stats"]["S_base|all"]["alpha_k"] == 0.03 * 0.04
        assert p2["stats"]["S_mk1|all"]["alpha_k"] == 0.01 * 0.04 and p2["stats"]["L_base|all"]["alpha_k"] == 0.01 * 0.04               # L_base — решающий член
        assert all(p2["stats"][k]["decision"] == "описательно" and p2["stats"][k]["alpha_k"] == 0.0 for k in KEYS if not k.endswith("|all"))
        assert p2["point"]["id"] == "L2" and p2["bin_md5"] == sha_of(BIN_BYTES)[1] and p2["freeze_commit"] == "c0ffee"
        n1 = {c: len(v) for c, v in tabs["rows"].items()}
        tabs2 = json.load(open(os.path.join(e.fwd, "tables-L2.json"), encoding="utf-8"))
        assert all(len(tabs2["rows"][c]) > n1[c] for c in n1)
        expect_guard(lambda: run(e, "L2", D("2027-01-01"), D("2026-12-31")), "повторное чтение запрещено")
        txt = fw.render(p2)
        assert "решение" in txt and "подтверждено" in txt and "| S_base | crypto |" in txt and "| L_base | tradfi |" in txt


def test_run_point_guards_stop_before_the_marker_and_the_engine():
    with tmp() as t:
        e = Env(t)
        prepare(e, "L1", D("2026-11-30"))
        r = FakeRunner(D("2026-11-30"))

        def attempt(pid="L1", today=D("2026-12-01"), **kw):
            return lambda: fw.run_point(e.args(pid, **kw), runner=r, today=today, repo=e.root)

        marker = os.path.join(e.fwd, "READ-L1.started")
        expect_guard(attempt(go=False), "TK616F_GO=1 не задан")
        expect_guard(attempt(freeze=os.path.join(t, "нет.json")), "нет файла заморозки")
        expect_guard(attempt(freeze_commit=""), "нужны --freeze-commit")
        expect_guard(attempt(freeze_sha256="0" * 64), "≠ заданному")
        expect_guard(attempt(pid="L9"), "не из заморозки")
        expect_guard(attempt(today=D("2026-11-30")), "ещё не закончилась")
        other = os.path.join(t, "other-bin")
        open(other, "wb").write(b"other")
        expect_guard(attempt(bin=other), "≠ замороженным")
        late = os.path.join(e.fwd, "L1", "w1", "klines", COINS[0], "%s-klines-2026-12-02.zip" % COINS[0])
        open(late, "w").close()
        expect_guard(attempt(), "позже 2026-11-30")
        os.remove(late)
        alien = os.path.join(e.fwd, "L1", "w1", "klines", "ZZZUSDT")
        os.makedirs(alien)
        open(os.path.join(alien, "ZZZUSDT-klines-2026-11-01.zip"), "w").close()
        expect_guard(attempt(), "вне списка all")
        shutil.rmtree(alien)
        expect_guard(attempt(base_dir=os.path.join(t, "нет")), "нет базового списка")
        udir = os.path.join(e.fwd, "L1", "universe")
        keep = open(os.path.join(udir, "symbols-crypto.txt"), encoding="utf-8").read()
        write_list(os.path.join(udir, "symbols-crypto.txt"), CRYPTO[:2])                       # из списка точки пропали базовые монеты
        expect_guard(attempt(), "не содержит базовых монет")
        open(os.path.join(udir, "symbols-crypto.txt"), "w", encoding="utf-8").write(keep)
        os.remove(os.path.join(udir, "contracts.csv"))
        expect_guard(attempt(), "нет")
        prepare(e, "L1", D("2026-11-30"), extra=[("NEWAUSDT", False, "2026-11-10", "2026-11-30")])            # прирост с первыми сутками до 13.11
        expect_guard(attempt(), "вне (2026-11-13; 2026-11-30]")
        prepare(e, "L1", D("2026-11-30"))
        expect_guard(attempt(pid="L2", today=D("2027-01-01")), "нет таблиц точки L1")
        assert r.calls == 0 and not os.path.exists(marker) and not os.path.exists(os.path.join(e.fwd, "READ-L2.started"))


def test_run_point_stops_when_data_between_points_changed_or_manifest_is_missing():
    def after_l1(mutate, needle):
        with tmp() as t:
            e = Env(t)
            run(e, "L1", D("2026-12-01"), D("2026-11-30"))
            prepare(e, "L2", D("2026-12-31"))
            mutate(e)
            expect_guard(lambda: run(e, "L2", D("2027-01-01"), D("2026-12-31")), needle)
            assert not os.path.exists(os.path.join(e.fwd, "READ-L2.started"))

    day = "2026-11-05"
    f = lambda e: os.path.join(e.fwd, "L2", "w1", "aggtrades", COINS[1], "%s-aggtrades-%s.zip" % (COINS[1], day))
    after_l1(lambda e: open(f(e), "wb").write(b"republished"), "данные между точками изменились")                                    # републикация суток
    after_l1(lambda e: os.remove(f(e)), "данные между точками изменились")                                                           # сутки пропали
    after_l1(lambda e: os.remove(os.path.join(e.fwd, "files-L1.tsv")), "нет манифеста файлов точки L1")


def test_run_point_stops_on_prefix_difference_and_on_seam_difference():
    with tmp() as t:
        e = Env(t)
        run(e, "L1", D("2026-12-01"), D("2026-11-30"))
        pp = os.path.join(e.fwd, "tables-L1.json")
        tabs = json.load(open(pp, encoding="utf-8"))
        tabs["rows"]["S_base|crypto"][0][3] += 5.0                                          # прежняя точка «читалась» иначе: gross одной сделки (только вселенная crypto)
        json.dump(tabs, open(pp, "w", encoding="utf-8"))
        expect_guard(lambda: run(e, "L2", D("2027-01-01"), D("2026-12-31")), "префикс-охрана точки L2 против L1")
        assert os.path.exists(os.path.join(e.fwd, "READ-L2.started")) and not os.path.exists(os.path.join(e.fwd, "READ-L2.done"))
    with tmp() as t:
        e = Env(t)
        run(e, "L1", D("2026-12-01"), D("2026-11-30"))

        class SeamBreaker(FakeRunner):
            def trades(self, vid, side, t0d, syms):
                rows = super().trades(vid, side, t0d, syms)
                if t0d == D("2026-11-23") and TRADFI[0] in syms and set(syms) <= set(TRADFI + [NEW2[0]]):      # w2 видит перекрытие иначе, чем w1 — только во вселенной tradfi
                    rows = [(c, a, b, g + (3.0 if D("2026-12-25") <= fw.date_of(a + fw.t0_ms(t0d)) <= D("2026-12-27") else 0.0), f, u, k) for c, a, b, g, f, u, k in rows]
                return rows

        expect_guard(lambda: run(e, "L2", D("2027-01-01"), D("2026-12-31"), runner=SeamBreaker(D("2026-12-31"))), "G3 швов окон")


def test_main_plan_marks_read_points_and_point_without_go_exits_with_code_3():
    with tmp() as t:
        e = Env(t)
        assert fw.main(["plan", "--freeze", e.freeze, "--root", e.fwd]) == 0
        os.makedirs(e.fwd)
        open(os.path.join(e.fwd, "READ-L1.started"), "w").close()
        out = subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(HERE, "..", "tk616_forward.py"), "plan", "--freeze", e.freeze, "--root", e.fwd], capture_output=True, text=True,
                             encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
        assert out.returncode == 0 and "точка L1" in out.stdout and "прочитана" in out.stdout and "не читалась" in out.stdout
        assert "α(S_base) 0.0012" in out.stdout and "нет (описательно)" in out.stdout and "решающая all" in out.stdout
        os.environ.pop(fw.GO_ENV, None)
        assert fw.main(["point", "L2", "--freeze", e.freeze, "--root", e.fwd, "--bin", e.bin]) == 3
        assert fw.main(["point", "--freeze", e.freeze, "--root", e.fwd]) == 3


# ---- TK-638: кандидат вперёд `L_n24` (C1 α из кандидата, C2 охрана таблицы, C3 replay-n24) --------------------------------------------------------------------

FEAT_BYTES = b"synthetic-alpha-feat"


def make_candidate(e, mutate=None):
    """Кандидат `tk638-frozen-1` против заморозки среды `e`: α S_base 0,025 + L_n24 0,005 (сумма 0,05), клетка = L_base + `fa=N24_q60 fo=lo fv=0.5`; пишется рядом с заморозкой."""
    f = json.loads(json.dumps(e.fz))                                                                  # копия: мутации охранных тестов не должны менять общие структуры сборщика заморозки
    base = f["cells"]["L_base"]
    keys = base["keys"] + " fa=N24_q60 fo=lo fv=0.5"
    alpha = {"S_base": 0.025, "S_mk1": 0.01, "L_base": 0.01, "L_n24": 0.005}
    sha, md5 = sha_of(FEAT_BYTES)
    c = {"version": "tk638-frozen-1", "base_freeze": {"version": "tk626-frozen-1", "sha256": fw.sha256_text(e.freeze)}, "binary": f["binary"], "feature_binary": {"md5": md5, "sha256": sha},
         "universe": f["universe"], "point_shares": f["point_shares"], "points": f["points"], "windows": f["windows"], "filter": {"name": "N24_q60", "N": 220000},
         "cells": {"L_n24": {"side": "L", "base_keys": base["keys"], "base_vid": base["vid"], "keys": keys, "vid": rf.Req("L", keys, False).vid, "family": False, "candidate": True}},
         "alpha": alpha, "alpha_k": {k: {p: v * s for p, s in f["point_shares"].items()} for k, v in alpha.items()}}
    if mutate:
        mutate(c)
    path = fw.candidate_path(e.freeze)
    json.dump(c, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    e.feat = os.path.join(e.root, "alpha-feat")
    open(e.feat, "wb").write(FEAT_BYTES)
    return c, path


def cand_kw(e, path, **kw):
    out = dict(feat_bin=e.feat, candidate="", candidate_commit="c0ffee", candidate_sha256=fw.sha256_text(path))
    out.update(kw)
    return out


class N24Runner(FakeRunner):
    """+ сигнальный дамп (`P42_SIGSX_ONLY`), `alpha-feat tcount` и вызов движка с `--ftab`. Кандидаты потока — функция (поток, монета, сутки); n24 — функция (монета, абс. время);
    `m24_days` — {монета: сутки}, где файла суток нет (m24 = 1, n24 = 0); `extra_cands` — кандидатов движка сверх таблицы (no_key); `short_counts` — tcount теряет строку."""

    def __init__(self, last_day, m24_days=None, extra_cands=0, short_counts=False, **kw):
        super().__init__(last_day, **kw)
        self.m24_days, self.extra_cands, self.short_counts, self.log = m24_days or {}, extra_cands, short_counts, []

    def stream(self, svid, t0d, syms):
        t0ms = fw.t0_ms(t0d)
        out = []
        for k in range(rf.GRID_START_DAY, rf.TRADE_END + 1):
            day = t0d + datetime.timedelta(days=k)
            if day > self.last_day:
                break
            for c in syms:
                if c in FIRST and day < D(FIRST[c]):
                    continue
                x = h(svid, c, day)
                if x % 2 == 0:
                    out.append((c, fw.t0_ms(day) + (x >> 4) % 20 * 3_600_000 - t0ms))
        return out

    def m24(self, sym, abs_ms):
        return fw.date_of(abs_ms) in self.m24_days.get(sym, ())

    def n24(self, sym, abs_ms):
        return 0 if self.m24(sym, abs_ms) else 100_000 + h("n24", sym, abs_ms) % 260_000

    def run(self, cmd, env=None, log=None):
        self.log.append((cmd, env))
        if len(cmd) > 1 and cmd[1] == "tcount":
            return self.tcount(cmd)
        if env and env.get("P42_SIGSX_ONLY") == "1":
            return self.dump(cmd, env)
        if "--ftab" in cmd:
            return self.engine_ftab(cmd, env)
        return super().run(cmd, env, log)

    def dump(self, cmd, env):
        self.calls += 1
        vfile, out, syms = cmd[cmd.index("--variants") + 1], cmd[cmd.index("--out") + 1], cmd[cmd.index("--symbols") + 1].split(",")
        for line in open(vfile, encoding="utf-8"):
            vid = line.split(" ", 1)[0]
            os.makedirs(os.path.join(out, vid), exist_ok=True)
            with open(os.path.join(out, vid, "long-sigsx.csv"), "w", encoding="utf-8", newline="") as f:
                f.write("sym,level,sig_ms,dist_e8,level_px_e8,born,end,first_bar,members\n")                      # как у бинаря: 9 имён в заголовке, 10 полей в строках
                for c, ms in self.stream(vid, D(env["P42_T0"]), syms):
                    f.write("%s,0,%d,0,0,0,0,0,0,m\n" % (c, ms))
        return 0

    def tcount(self, cmd):
        self.calls += 1
        eps, out, base = (cmd[cmd.index(k) + 1] for k in ("--eps", "--out", "--base-date"))
        t0ms = fw.t0_ms(D(base))
        rows = [line.rstrip("\n").split(",") for line in open(eps, encoding="utf-8")][1:]
        if self.short_counts:
            rows = rows[:-1]
        with open(out, "w", encoding="utf-8", newline="") as f:
            f.write("sym,sig_ms,n60,n24,m60,m24\n")
            for s, m in rows:
                f.write("%s,%s,0,%d,0,%d\n" % (s, m, self.n24(s, t0ms + int(m)), int(self.m24(s, t0ms + int(m)))))
        return 0

    def engine_ftab(self, cmd, env):
        self.calls += 1
        vfile, out, tab, syms = cmd[cmd.index("--variants") + 1], cmd[cmd.index("--out") + 1], cmd[cmd.index("--ftab") + 1], cmd[cmd.index("--symbols") + 1].split(",")
        t0d = D(env["P42_T0"])
        t0ms = fw.t0_ms(t0d)
        lim = fw.t0_ms(min(self.last_day, t0d + datetime.timedelta(days=rf.TRADE_END))) + DAY
        tsyms, tms, cols = rr.rfx.read_ftab(tab)
        val = {(s, int(m)): cols["N24_q60"][i] for i, (s, m) in enumerate(zip(tsyms, tms))}
        for line in open(vfile, encoding="utf-8"):
            vid, keys = line.rstrip("\n").split(" ", 1)
            stream = self.stream(rr.Engine.stream_need(rf.Req("L", keys, False)).vid, t0d, syms) + [("ZZZUSDT", 7 + i) for i in range(self.extra_cands)]
            kept, dropped, no_key = [], 0, 0
            for c, m in stream:
                v = val.get((c, m))
                if v is None:
                    no_key += 1
                elif v < 0.5:
                    dropped += 1
                    continue
                kept.append((c, m))
            os.makedirs(os.path.join(out, vid), exist_ok=True)
            tr = []
            for c, m in kept:
                ent = t0ms + m
                ext, kind = ent + 2 * 3_600_000, "tp"
                if ext > lim:
                    ext, kind = lim, "forced"
                tr.append((c, m, ext - t0ms, 1.0 + h("g", c, ent) % 100 / 50.0, 0.3, kind))
            with open(os.path.join(out, vid, "long-trades.csv"), "w", encoding="utf-8", newline="") as f:
                f.write("sym,entry_ms,exit_ms,gross,fees,funding,net,kind\n")
                for c, a, b, g, fee, kind in tr:
                    f.write("%s,%d,%d,%r,%r,0.0,%r,%s\n" % (c, a, b, g, fee, g - fee, kind))
            for name in ("daily", "coinday", "entryrej"):
                open(os.path.join(out, vid, "long-%s.csv" % name), "w", encoding="utf-8", newline="").write("%s,%d,%d\n" % (name, len(tr), dropped))
            json.dump({"long": {"filter_[cands,dropped,no_key,no_value]": [len(stream), dropped, no_key, 0]}}, open(os.path.join(out, vid, "meta.json"), "w"))
        return 0


def run_c(e, pid, today, last_day, runner=None, **kw):
    """Точка с кандидатом: как `run`, но движок — `N24Runner`."""
    return run(e, pid, today, last_day, runner=runner or N24Runner(last_day), **kw)


def cand_env(t, mutate=None):
    e = Env(t)
    c, path = make_candidate(e, mutate)
    return e, c, path


def read_json(*p):
    return json.load(open(os.path.join(*p), encoding="utf-8"))


def test_candidate_real_files_alpha_split_and_overlay():
    """Реальная пара `tk626-frozen.json` + `tk638-frozen.json` репозитория: α S_base 0,025 (при 0,03 тест падает), сумма 0,05, L_n24 0,005; клетка `L_n24` не входит в `cells`."""
    fz0 = fw.load_freeze(fw.FREEZE)
    cp = fw.candidate_path(fw.FREEZE)
    assert os.path.basename(cp) == "tk638-frozen.json"
    c = fw.load_candidate(cp, fz0, fw.FREEZE)
    assert c["alpha"] == {"S_base": 0.025, "S_mk1": 0.01, "L_base": 0.01, "L_n24": 0.005} and abs(sum(c["alpha"].values()) - 0.05) < 1e-12
    assert fz0["alpha"]["S_base"] == 0.03                                                              # историческая запись tk626 не меняется
    fz = fw.apply_candidate(fz0, c)
    assert fz["alpha"]["S_base"] == 0.025 and fz["alpha"]["S_base"] != 0.03 and fz["alpha"]["L_n24"] == 0.005 and fz0["alpha"]["S_base"] == 0.03
    assert "L_n24" not in fz["cells"] and list(fz["cells"]) == list(fz0["cells"])
    assert abs(fw.alpha_k(fz, "S_base", "L5") - 0.025 * 0.66) < 1e-12 and abs(fw.alpha_k(fz, "L_n24", "L2") - 0.005 * 0.04) < 1e-12
    assert fw.alpha_k(fz, "S_mk1", "L3") == fw.alpha_k(fz0, "S_mk1", "L3") and fw.alpha_k(fz, "L_base", "L3") == fw.alpha_k(fz0, "L_base", "L3")
    assert c["cells"]["L_n24"]["keys"] == fz0["cells"]["L_base"]["keys"] + " fa=N24_q60 fo=lo fv=0.5" and c["cells"]["L_n24"]["vid"] == "c2f7cf2fd5d43"
    assert c["filter"]["N"] == 220000 and c["feature_binary"]["md5"] == "69fcf7cdc74a8006e78681d29a980f68"


def test_candidate_guards_every_rule_of_c1():
    def bad(needle, mutate):
        with tmp() as t:
            e, c, path = cand_env(t, mutate)
            expect_guard(lambda: fw.load_candidate(path, fw.load_freeze(e.freeze), e.freeze), needle)

    with tmp() as t:
        e, c, path = cand_env(t)
        fz = fw.load_freeze(e.freeze)
        assert fw.load_candidate(path, fz, e.freeze)["alpha"]["L_n24"] == 0.005
        expect_guard(lambda: fw.load_candidate(os.path.join(t, "нет.json"), fz, e.freeze), "нет файла кандидата")
        open(e.freeze, "a").write("\n ")                                                               # заморозка tk626 изменена после записи кандидата
        expect_guard(lambda: fw.load_candidate(path, fz, e.freeze), "записан против другой заморозки")
    bad("версия кандидата", lambda c: c.update(version="tk638-frozen-2"))
    bad("записан против другой заморозки", lambda c: c["base_freeze"].update(sha256="0" * 64))
    bad("поле 'binary'", lambda c: c["binary"].update(md5="0" * 32))
    bad("поле 'windows'", lambda c: c["windows"][0].update(t0="2026-10-17"))
    bad("поле 'points'", lambda c: c["points"][1].update(d="2026-12-30"))
    bad("поле 'point_shares'", lambda c: c["point_shares"].update(L5=0.6))
    bad("поле 'universe'", lambda c: c["universe"].update(decision="crypto"))
    bad("md5 / sha256 `alpha-feat`", lambda c: c["feature_binary"].pop("md5"))
    bad("фильтр без имени", lambda c: c["filter"].update(N=0))
    bad("клетки", lambda c: c["cells"].update(X_extra=dict(c["cells"]["L_n24"])))
    bad("клетка L_n24", lambda c: c["cells"]["L_n24"].update(keys=c["cells"]["L_n24"]["keys"].replace("fv=0.5", "fv=0.4")))
    bad("клетка L_n24", lambda c: c["cells"]["L_n24"].update(family=True))
    bad("клетка L_n24", lambda c: c["cells"]["L_n24"].update(base_vid="x"))
    bad("vid", lambda c: c["cells"]["L_n24"].update(vid="c2f7cf2fd5d44"))
    bad("α кандидата заданы", lambda c: c["alpha"].pop("L_n24"))
    bad("сумма α кандидата", lambda c: c["alpha"].update(S_base=0.03))                                  # 0,03 остаётся у S_base: сумма 0,055
    bad("α: S_base", lambda c: c["alpha"].update(L_n24=0.006, L_base=0.009))                           # сумма 0,05, но S_base + L_n24 ≠ 0,03
    bad("α(S_mk1)", lambda c: c["alpha"].update(S_mk1=0.011, L_base=0.009))
    bad("α по точкам", lambda c: c["alpha_k"]["S_base"].update(L5=0.0198))


def test_run_point_with_candidate_overlays_alpha_marks_the_read_and_guards_the_candidate_files():
    with tmp() as t:
        e, c, cp = cand_env(t)
        prepare(e, "L1", D("2026-11-30"))
        r = N24Runner(D("2026-11-30"))

        def attempt(**kw):
            return lambda: fw.run_point(e.args("L1", **cand_kw(e, cp, **kw)), runner=r, today=D("2026-12-01"), repo=e.root)

        expect_guard(attempt(feat_bin=""), "--feat-bin")
        other = os.path.join(t, "feat-other")
        open(other, "wb").write(FEAT_BYTES + b"x")
        expect_guard(attempt(feat_bin=other), "alpha-feat")
        expect_guard(attempt(candidate_commit=""), "нужны --freeze-commit")
        expect_guard(attempt(candidate_sha256="0" * 64), "≠ заданному")
        expect_guard(attempt(candidate=os.path.join(t, "нет.json")), "нет файла кандидата")
        assert r.calls == 0 and not os.path.exists(os.path.join(e.fwd, "READ-L1.started"))
        assert attempt()() == 0
        marker = read_json(e.fwd, "READ-L1.started")
        assert marker["candidate"] == "L_n24" and marker["candidate_commit"] == "c0ffee" and marker["candidate_sha256"] == fw.sha256_text(cp) and marker["feat_bin_md5"] == sha_of(FEAT_BYTES)[1]
        p1 = read_json(e.fwd, "point-L1.json")
        assert set(p1["stats"]) == set(KEYS) | {"L_n24|%s" % u for u in fw.UNI_NAMES} and set(p1["n24"]) == set(fw.UNI_NAMES) and all(x["status"] == "read" for x in p1["n24"].values())
        assert os.path.exists(os.path.join(e.fwd, "READ-L1.n24.json"))
    with tmp() as t:
        e, c, cp = cand_env(t)
        run_c(e, "L1", D("2026-12-01"), D("2026-11-30"), **cand_kw(e, cp))
        rc, r = run_c(e, "L2", D("2027-01-01"), D("2026-12-31"), **cand_kw(e, cp))
        p2 = read_json(e.fwd, "point-L2.json")
        s = p2["stats"]
        assert abs(s["S_base|all"]["alpha_k"] - 0.025 * 0.04) < 1e-12 and s["S_base|all"]["alpha_k"] != 0.03 * 0.04                  # C1: α(S_base) из кандидата, не 0,03
        assert abs(s["L_n24|all"]["alpha_k"] - 0.005 * 0.04) < 1e-12 and abs(s["S_mk1|all"]["alpha_k"] - 0.01 * 0.04) < 1e-12 and abs(s["L_base|all"]["alpha_k"] - 0.01 * 0.04) < 1e-12
        assert s["L_n24|crypto"]["alpha_k"] == 0.0 and s["L_n24|crypto"]["decision"] == "описательно" and s["L_n24|all"]["n"] > 0                # решающая вселенная — all
        assert p2["prefix"]["diffs"] == {k: 0 for k in list(KEYS) + ["L_n24|%s" % u for u in fw.UNI_NAMES]} and p2["seams"]["L_n24|all"] == 0
        txt = fw.render(p2)
        assert "L_n24 (кандидат TK-638), вселенная all: прочитана" in txt and "| L_n24 | tradfi |" in txt


def test_n24_table_and_filter_semantics_are_exact():
    """Таблица `--ftab`: 0,0 при n24 < N и не m24, 1,0 иначе (m24 = 1 проходит); движок отбрасывает ровно нули; сделки клетки = кандидаты с 1,0; охрана читает дамп по позициям."""
    with tmp() as t:
        e, c, cp = cand_env(t)
        prepare(e, "L1", D("2026-11-30"))
        flagged = {"AAAUSDT": {D("2026-10-30") + datetime.timedelta(days=i) for i in range(10)}}        # 30.10–08.11: до зачёта (17.11–26.11): m24 = 1, n24 = 0 — в доле не участвуют
        r = N24Runner(D("2026-11-30"), m24_days=flagged)
        fw.run_point(e.args("L1", **cand_kw(e, cp)), runner=r, today=D("2026-12-01"), repo=e.root)
        need = rr.Engine.stream_need(fw.n24_req(c))
        tab = os.path.join(e.fwd, "L1", "ftab", "all", "w1", need.vid, "ftab.csv")
        syms, ms, cols = rr.rfx.read_ftab(tab)
        t0ms = fw.t0_ms(D("2026-10-16"))
        want = [1.0 if r.m24(s, t0ms + int(m)) or r.n24(s, t0ms + int(m)) >= 220000 else 0.0 for s, m in zip(syms, ms)]
        assert list(cols["N24_q60"]) == want and 0.0 in want and 1.0 in want and open(tab, encoding="utf-8").read().startswith("sym,sig_ms,N24_q60\n")
        assert any(r.m24(s, t0ms + int(m)) and r.n24(s, t0ms + int(m)) < 220000 for s, m in zip(syms, ms))      # проверка самого теста: есть строка «m24 = 1 при n24 < N», она проходит
        info = read_json(e.fwd, "READ-L1.n24.json")["all"]
        w = info["windows"]["w1"]
        assert info["status"] == "read" and w["rows"] == len(syms) and w["excluded"] == want.count(0.0) and w["m24_credited"] == 0 and w["m24_all"] > 0 and info["m24_share"] == 0.0 and info["no_key"] == 0
        assert w["engine"]["cands"] == len(syms) and w["engine"]["dropped"] == want.count(0.0) and w["engine"]["no_key"] == 0
        tr = rf.read_trades_csv(os.path.join(e.fwd, "L1", "out", "all", "w1", c["cells"]["L_n24"]["vid"], "long-trades.csv"))
        kept = {(s, int(m)) for s, m, v in zip(syms, ms, want) if v == 1.0}
        assert len(tr) == len(kept) and {(tr.sym[i], int(tr.entry_ms[i])) for i in range(len(tr))} == kept
        dump_call = [x for x in r.log if x[1] and x[1].get("P42_SIGSX_ONLY") == "1" and x[1]["P42_T0"] == "2026-10-16"]
        assert dump_call and dump_call[0][1]["P42_SIGSX"] == "14:73" and "--ftab" not in dump_call[0][0]
        tc = [x for x in r.log if len(x[0]) > 1 and x[0][1] == "tcount"][0][0]
        assert tc[0] == e.feat and tc[tc.index("--base-date") + 1] == "2026-10-16" and tc[tc.index("--trades") + 1].endswith(os.path.join("L1", "w1", "aggtrades"))
        assert w["credited_range"] == ["2026-11-17", "2026-11-26"]


def flagged_days(r, svid, coin, t0d, lo, hi):
    """Сутки в [lo, hi], где у монеты есть кандидат потока в окне t0d."""
    return sorted({fw.date_of(fw.t0_ms(t0d) + m) for c, m in r.stream(svid, t0d, [coin]) if lo <= fw.date_of(fw.t0_ms(t0d) + m) <= hi})


def test_n24_m24_share_gate_is_per_universe_and_leaves_other_cells_unchanged():
    def point_l2(flag):
        with tmp() as t:
            e, c, cp = cand_env(t)
            run_c(e, "L1", D("2026-12-01"), D("2026-11-30"), runner=N24Runner(D("2026-11-30")), **cand_kw(e, cp))
            run_c(e, "L2", D("2027-01-01"), D("2026-12-31"), runner=N24Runner(D("2026-12-31"), m24_days={"AAAUSDT": set(flag)}), **cand_kw(e, cp))
            return c, read_json(e.fwd, "point-L2.json"), fw.render(read_json(e.fwd, "point-L2.json"))

    c, base, _ = point_l2([])
    svid = rr.Engine.stream_need(fw.n24_req(c)).vid
    days = flagged_days(N24Runner(D("2026-12-31")), svid, "AAAUSDT", D("2026-10-16"), D("2026-12-01"), D("2026-12-24"))           # сутки после данных L1 (30.11): между точками данные не меняются
    assert len(days) >= 3 and all(base["n24"][u]["status"] == "read" and base["n24"][u]["m24_share"] == 0.0 for u in fw.UNI_NAMES)
    _, p1, _ = point_l2(days[:1])                                    # 1 кандидат с m24 = 1: доля ≤ 1 % на all и crypto → клетка читается
    for u in ("all", "crypto"):
        x = p1["n24"][u]
        cr = sum(w["credited"] for w in x["windows"].values())
        assert x["status"] == "read" and 0 < x["m24_share"] <= 0.01 and abs(x["m24_share"] - 1 / cr) < 1e-12 and sum(w["m24_credited"] for w in x["windows"].values()) == 1
    _, p3, txt = point_l2(days[:3])                                  # 3 кандидата: > 1 % на all и crypto — не читать; tradfi (монеты нет) — читается
    assert p3["n24"]["all"]["status"] == "skip" and p3["n24"]["crypto"]["status"] == "skip" and p3["n24"]["tradfi"]["status"] == "read"
    assert p3["n24"]["all"]["m24_share"] > 0.01 and "> 1 %" in p3["n24"]["all"]["reason"] and "не читается" in p3["n24"]["all"]["reason"]
    assert "L_n24|all" not in p3["stats"] and "L_n24|crypto" not in p3["stats"] and "L_n24|tradfi" in p3["stats"]
    assert abs(p3["stats"]["S_base|all"]["alpha_k"] - 0.025 * 0.04) < 1e-12 and abs(base["stats"]["S_base|all"]["alpha_k"] - 0.025 * 0.04) < 1e-12        # α(S_base) 0,025 и при пропуске L_n24 (α уже потрачены)
    for k in KEYS:                                                   # остальные клетки — те же числа, что без сбоя
        assert p3["stats"][k] == base["stats"][k] and p1["stats"][k] == base["stats"][k], k
    assert p3["prefix"]["diffs"] == {k: 0 for k in list(KEYS) + ["L_n24|tradfi"]}                  # префикс-охрана — только по клеткам, посчитанным сейчас (L_n24|all на L1 был)
    assert "L_n24 (кандидат TK-638), вселенная all: НЕ ЧИТАЛАСЬ" in txt and "m24 = 1" in txt
    _, far, _ = point_l2([D("2026-11-05"), D("2026-11-06"), D("2026-11-07")])                      # m24 до зачёта: в таблице есть, в долю не входит
    assert far["n24"]["all"]["status"] == "read" and far["n24"]["all"]["m24_share"] == 0.0 and far["n24"]["all"]["windows"]["w1"]["m24_all"] > 0


def test_n24_no_key_and_row_mismatch_skip_the_cell_only():
    for runner_kw, needle in (({"extra_cands": 2}, "no_key = 2"), ({"short_counts": True}, "строк tcount")):
        with tmp() as t:
            e, c, cp = cand_env(t)
            rc, _ = run_c(e, "L1", D("2026-12-01"), D("2026-11-30"), runner=N24Runner(D("2026-11-30"), **runner_kw), **cand_kw(e, cp))
            p1 = read_json(e.fwd, "point-L1.json")
            assert rc == 0 and set(p1["stats"]) == set(KEYS) and all(x["status"] == "skip" and needle in x["reason"] for x in p1["n24"].values())
            if "extra_cands" in runner_kw:
                assert all(x["no_key"] == 2 for x in p1["n24"].values()) and "no_key 2" in fw.render(p1)                  # no_key печатается на точке
            assert os.path.exists(os.path.join(e.fwd, "READ-L1.done")) and p1["stats"]["S_base|all"]["n"] > 0


def test_n24_cell_skipped_on_a_later_point_or_read_after_a_skip_does_not_break_the_prefix_guard():
    with tmp() as t:
        e, c, cp = cand_env(t)
        run_c(e, "L1", D("2026-12-01"), D("2026-11-30"), runner=N24Runner(D("2026-11-30")), **cand_kw(e, cp))
        rc, _ = run_c(e, "L2", D("2027-01-01"), D("2026-12-31"), runner=N24Runner(D("2026-12-31"), extra_cands=1), **cand_kw(e, cp))
        p2 = read_json(e.fwd, "point-L2.json")
        assert rc == 0 and all(x["status"] == "skip" for x in p2["n24"].values()) and "L_n24|all" not in p2["stats"] and p2["prefix"]["diffs"] == {k: 0 for k in KEYS}
    with tmp() as t:
        e, c, cp = cand_env(t)
        run_c(e, "L1", D("2026-12-01"), D("2026-11-30"), runner=N24Runner(D("2026-11-30"), short_counts=True), **cand_kw(e, cp))
        rc, _ = run_c(e, "L2", D("2027-01-01"), D("2026-12-31"), runner=N24Runner(D("2026-12-31")), **cand_kw(e, cp))
        p2 = read_json(e.fwd, "point-L2.json")
        assert rc == 0 and all(x["status"] == "read" for x in p2["n24"].values()) and p2["stats"]["L_n24|all"]["n"] > 0


def test_missing_candidate_file_stops_the_read_and_no_candidate_keeps_alpha_without_reading_l_n24():
    with strict(), tmp() as t:
        e = Env(t)                                                                                 # tk638-frozen.json рядом с заморозкой нет
        prepare(e, "L1", D("2026-11-30"))
        r = N24Runner(D("2026-11-30"))
        marker = os.path.join(e.fwd, "READ-L1.started")
        expect_guard(lambda: fw.run_point(e.args("L1"), runner=r, today=D("2026-12-01"), repo=e.root), "нет файла кандидата")
        expect_guard(lambda: fw.run_point(e.args("L1", no_candidate=True), runner=r, today=D("2026-12-01"), repo=e.root), "нет файла кандидата")      # флаг файл не заменяет
        ra = argparse.Namespace(freeze=e.freeze, freeze_commit="c0ffee", freeze_sha256=fw.sha256_text(e.freeze), candidate="", candidate_commit="", candidate_sha256="", feat_bin="", bin=e.bin, ref="")
        expect_guard(lambda: fw.replay_n24(ra, runner=r, repo=e.root), "нет файла кандидата")
        assert r.calls == 0 and not os.path.exists(marker)
    with tmp() as t:
        e, c, cp = cand_env(t)
        prepare(e, "L1", D("2026-11-30"))
        r = N24Runner(D("2026-11-30"))
        expect_guard(lambda: fw.replay_n24(replay_args(e, cp, t, "", no_candidate=True), runner=r, repo=e.root), "без --no-candidate")
        rc = fw.run_point(e.args("L1", **cand_kw(e, cp, no_candidate=True, feat_bin="")), runner=r, today=D("2026-12-01"), repo=e.root)          # alpha-feat не нужен: L_n24 не читается
        assert rc == 0 and r.calls == 3 and all("tcount" not in x[0] and "--ftab" not in x[0] and not (x[1] or {}).get("P42_SIGSX_ONLY") for x in r.log)
        marker = read_json(e.fwd, "READ-L1.started")
        assert marker["candidate_read"] is False and "кандидат не читан" in marker["candidate_note"] and marker["candidate_commit"] == "c0ffee" and "feat_bin_md5" not in marker
        p1 = read_json(e.fwd, "point-L1.json")
        assert set(p1["stats"]) == set(KEYS) and p1["n24"] == {} and not os.path.exists(os.path.join(e.fwd, "READ-L1.n24.json"))
        assert "кандидат не читан" in fw.render(p1)
        rc2, _ = run_c(e, "L2", D("2027-01-01"), D("2026-12-31"), runner=N24Runner(D("2026-12-31")), **cand_kw(e, cp, no_candidate=True))
        p2 = read_json(e.fwd, "point-L2.json")
        assert rc2 == 0 and abs(p2["stats"]["S_base|all"]["alpha_k"] - 0.025 * 0.04) < 1e-12 and "L_n24|all" not in p2["stats"]          # α(S_base) 0,025, не 0,03


def test_plan_prints_the_candidate_alpha():
    def plan(e):
        return subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(HERE, "..", "tk616_forward.py"), "plan", "--freeze", e.freeze, "--root", e.fwd], capture_output=True, text=True,
                              encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))

    with tmp() as t:
        e, c, cp = cand_env(t)
        out = plan(e)
        assert out.returncode == 0 and "tk638-frozen.json (tk638-frozen-1)" in out.stdout and "'L_n24': 0.005" in out.stdout and "α(S_base) 0.0010" in out.stdout, out.stdout + out.stderr
    with tmp() as t:
        assert "кандидата tk638-frozen.json рядом с заморозкой нет — чтение точки остановится" in plan(Env(t)).stdout


def replay_args(e, cp, root, ref, **kw):
    a = dict(freeze=e.freeze, freeze_commit="c0ffee", freeze_sha256=fw.sha256_text(e.freeze), bin=e.bin, threads=1, tick_store="", sig_cache="", **cand_kw(e, cp),
             replay_root=os.path.join(root, "replay"), windows_root=os.path.join(root, "win"), symbols_file=os.path.join(root, "symbols-all.txt"), ref=ref, out="")
    a.update(kw)
    return argparse.Namespace(**a)


def test_replay_n24_compares_outputs_with_the_g638a_reference_byte_for_byte():
    with tmp() as t:
        e, c, cp = cand_env(t)
        root = os.path.join(t, "r")
        os.makedirs(root)
        write_list(os.path.join(root, "symbols-all.txt"), COINS)
        mk_tree(os.path.join(root, "win"), "", rf.WINDOWS, D("2026-11-13"), coins=COINS)
        ref_path = os.path.join(root, "gate-638a.json")

        def write_ref(shas):
            ref = {"bin_md5": sha_of(BIN_BYTES)[1], "keys": c["cells"]["L_n24"]["keys"], "ok": True,
                   "windows": {w.name: {"files": {f: {"ref_sha256": shas.get((w.name, f), "0" * 64)} for f in fw.FILES_638}} for w in rf.WINDOWS}}
            json.dump(ref, open(ref_path, "w"))

        def replay(name, **kw):
            return fw.replay_n24(replay_args(e, cp, root, ref_path, replay_root=os.path.join(root, name), **kw), runner=N24Runner(D("2026-11-13")), repo=e.root)

        write_ref({})                                                                              # эталон ≠ выходам: ворота не пройдены, код 3, итог записан
        assert replay("replay") == 3
        res = read_json(root, "replay", "gate-638c.json")
        assert res["ok"] is False and all(not v["equal"] for v in res["windows"].values()) and res["n24"]["status"] == "read" and res["feat_bin_md5"] == sha_of(FEAT_BYTES)[1]
        assert os.path.exists(os.path.join(root, "replay", "wa"))
        shas = {(w, f): x["sha256"] for w, v in res["windows"].items() for f, x in v["files"].items()}
        assert len(shas) == 12 and len(set(shas.values())) > 3
        write_ref(shas)                                                                            # эталон = выходам (чистый корень): итог ok, код 0
        out2 = os.path.join(root, "gate-638c-2.json")
        assert replay("replay2", out=out2) == 0
        res2 = read_json(out2)
        assert res2["ok"] is True and all(v["equal"] for v in res2["windows"].values()) and res2["keys"] == c["cells"]["L_n24"]["keys"] and res2["candidate_commit"] == "c0ffee"
        assert res2["n24"]["m24_share"] == 0.0 and res2["n24"]["no_key"] == 0 and set(res2["n24"]["windows"]) == {"wa", "wb", "wc"}
        assert [x["credited_range"] for x in res2["n24"]["windows"].values()] == [[w.lo, w.hi] for w in rf.WINDOWS]       # replay: весь зачёт окна
        shas[("wb", "long-trades.csv")] = "1" * 64                                                 # одно различие в одном файле — не пройдено
        write_ref(shas)
        assert replay("replay3", out=os.path.join(root, "g3.json")) == 3
        res3 = read_json(root, "g3.json")
        assert res3["windows"]["wb"]["equal"] is False and res3["windows"]["wa"]["equal"] and res3["windows"]["wc"]["equal"] and res3["windows"]["wb"]["files"]["long-daily.csv"]["equal"]
        bad_ref = json.load(open(ref_path))
        bad_ref["keys"] += " x=1"
        json.dump(bad_ref, open(ref_path, "w"))
        expect_guard(lambda: replay("replay4"), "эталон G-638a")
        write_ref(shas)
        late = os.path.join(root, "win", "wc", "klines", COINS[0], "%s-klines-2026-11-14.zip" % COINS[0])                  # сутки ≥ 14.11 не читаются
        open(late, "w").close()
        expect_guard(lambda: replay("replay5"), "позже 2026-11-13")



# ---- TK-641: второй кандидат `L_mk2` (tk641-frozen.json; C1–C3 критика) --------------------------------------------------------------

@contextlib.contextmanager
def strict2():
    fw.REQUIRE_CANDIDATE2 = True
    try:
        yield
    finally:
        fw.REQUIRE_CANDIDATE2 = False


def make_candidate2(e, c, cpath, mutate=None):
    """Кандидат 2 `tk641-frozen-1` против заморозки и кандидата 1 среды `e`: α S_base 0,025 → 0,020 + L_mk2 0,005, клетка = L_base + `mk=2`; пишется рядом с заморозкой."""
    f = json.loads(json.dumps(e.fz))
    base = f["cells"]["L_base"]
    keys = fw.mk2_keys(base["keys"])
    alpha = dict(c["alpha"])
    alpha["S_base"] = round(alpha["S_base"] - 0.005, 10)
    alpha["L_mk2"] = 0.005
    c2 = {"version": "tk641-frozen-1", "base_freeze": {"version": "tk626-frozen-1", "sha256": fw.sha256_text(e.freeze)},
          "prev_candidate": {"version": "tk638-frozen-1", "sha256": fw.sha256_text(cpath)}, "binary": f["binary"], "universe": f["universe"], "point_shares": f["point_shares"],
          "points": f["points"], "windows": f["windows"],
          "cells": {"L_mk2": {"side": "L", "base_keys": base["keys"], "base_vid": base["vid"], "keys": keys, "vid": rf.Req("L", keys, False).vid, "family": False, "candidate": True}},
          "alpha": alpha, "alpha_k": {k: {p: v * s for p, s in f["point_shares"].items()} for k, v in alpha.items()}}
    if mutate:
        mutate(c2)
    path = fw.candidate2_path(e.freeze)
    json.dump(c2, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    return c2, path


def cand2_env(t, mutate=None):
    e, c, cp = cand_env(t)
    c2, cp2 = make_candidate2(e, c, cp, mutate)
    return e, c, cp, c2, cp2


def cand2_kw(e, cp, cp2, **kw):
    out = cand_kw(e, cp, candidate2="", candidate2_commit="c0ffee", candidate2_sha256=fw.sha256_text(cp2))
    out.update(kw)
    return out


def test_candidate2_real_files_chain_alpha_and_cell():
    """Реальная цепочка репозитория tk626 → tk638 → tk641: α S_base 0,020 (0,025 tk638 и 0,03 tk626 при наличии tk641 — провал), L_mk2 = L_base + mk=2, vid G-626c."""
    fz0 = fw.load_freeze(fw.FREEZE)
    cp, cp2 = fw.candidate_path(fw.FREEZE), fw.candidate2_path(fw.FREEZE)
    assert os.path.basename(cp2) == "tk641-frozen.json"
    c = fw.load_candidate(cp, fz0, fw.FREEZE)
    c2 = fw.load_candidate2(cp2, fz0, fw.FREEZE, c, cp)
    fz = fw.apply_candidate2(fw.apply_candidate(fz0, c), c2, True)
    assert fz["alpha"] == {"S_base": 0.02, "S_mk1": 0.01, "L_base": 0.01, "L_n24": 0.005, "L_mk2": 0.005} and abs(sum(fz["alpha"].values()) - 0.05) <= 1e-12
    assert fz["alpha"]["S_base"] not in (0.025, 0.03) and c["alpha"]["S_base"] == 0.025 and fz0["alpha"]["S_base"] == 0.03       # файлы tk626 / tk638 не меняются
    cell = c2["cells"]["L_mk2"]
    assert cell["vid"] == "cf280a1e8fc7c" and cell["keys"] == fz0["cells"]["L_base"]["keys"].replace(" t24=12", " mk=2 t24=12") and fz["cells"]["L_mk2"]["vid"] == "cf280a1e8fc7c"
    assert abs(fw.alpha_k(fz, "S_base", "L5") - 0.0132) < 1e-12 and abs(fw.alpha_k(fz, "S_base", "L2") - 0.0008) < 1e-12 and abs(fw.alpha_k(fz, "L_mk2", "L5") - 0.0033) < 1e-12
    assert all(abs(c2["alpha_k"][k][p] - c2["alpha"][k] * s) < 1e-12 for k in c2["alpha"] for p, s in fz0["point_shares"].items())
    assert "L_mk2" not in fw.apply_candidate2(fz0, c2, False)["cells"]


def test_candidate2_guards_every_rule():
    def bad(needle, mutate):
        with tmp() as t:
            e, c, cp, c2, cp2 = cand2_env(t, mutate)
            expect_guard(lambda: fw.load_candidate2(cp2, fw.load_freeze(e.freeze), e.freeze, c, cp), needle)

    with tmp() as t:
        e, c, cp, c2, cp2 = cand2_env(t)
        fz = fw.load_freeze(e.freeze)
        assert fw.load_candidate2(cp2, fz, e.freeze, c, cp)["alpha"]["S_base"] == 0.02
        expect_guard(lambda: fw.load_candidate2(cp2, fz, e.freeze, None, cp), "цепочка tk626 → tk638 → tk641 разорвана")
        expect_guard(lambda: fw.load_candidate2(os.path.join(t, "нет.json"), fz, e.freeze, c, cp), "нет файла кандидата")
        open(cp, "a").write("\n ")                                                                     # tk638 изменён после записи tk641
        expect_guard(lambda: fw.load_candidate2(cp2, fz, e.freeze, c, cp), "против другого tk638")
        open(e.freeze, "a").write("\n ")
        expect_guard(lambda: fw.load_candidate2(cp2, fz, e.freeze, c, cp), "против другой заморозки")
    bad("версия кандидата 2", lambda x: x.update(version="tk641-frozen-2"))
    bad("против другого tk638", lambda x: x["prev_candidate"].update(sha256="0" * 64))
    bad("против другого tk638", lambda x: x["prev_candidate"].update(version="tk638-frozen-2"))
    bad("поле 'binary'", lambda x: x["binary"].update(md5="0" * 32))
    bad("поле 'points'", lambda x: x["points"][1].update(d="2026-12-30"))
    bad("клетки", lambda x: x["cells"].update(X=dict(x["cells"]["L_mk2"])))
    bad("клетка L_mk2", lambda x: x["cells"]["L_mk2"].update(keys=x["cells"]["L_mk2"]["keys"].replace("mk=2", "mk=1")))
    bad("клетка L_mk2", lambda x: x["cells"]["L_mk2"].update(family=True))
    bad("vid", lambda x: x["cells"]["L_mk2"].update(vid="cf280a1e8fc7d"))
    bad("α кандидата 2 заданы", lambda x: x["alpha"].pop("L_n24"))
    bad("сумма α кандидата 2", lambda x: x["alpha"].update(S_base=0.025))                               # tk638 S_base 0,025 при наличии L_mk2: сумма 0,055
    bad("α: S_base", lambda x: x["alpha"].update(L_mk2=0.006, L_base=0.009))
    bad("α(S_mk1) кандидата 2", lambda x: x["alpha"].update(L_n24=0.006, S_mk1=0.009))
    bad("α по точкам", lambda x: x["alpha_k"]["S_base"].update(L5=0.0165))                              # α_k S_base от 0,025 вместо 0,020
    bad("α по точкам", lambda x: x["alpha_k"].pop("L_mk2"))


def test_run_point_with_candidate2_reads_l_mk2_with_its_alpha_and_marks_the_read():
    with strict(), strict2(), tmp() as t:
        e, c, cp, c2, cp2 = cand2_env(t)
        rc, r = run_c(e, "L1", D("2026-12-01"), D("2026-11-30"), **cand2_kw(e, cp, cp2))
        assert rc == 0
        marker = read_json(e.fwd, "READ-L1.started")
        assert marker["candidate2"] == "L_mk2" and marker["candidate2_read"] is True and marker["candidate2_sha256"] == fw.sha256_text(cp2) and marker["candidate"] == "L_n24"
        p1 = read_json(e.fwd, "point-L1.json")
        mk = {"L_mk2|%s" % u for u in fw.UNI_NAMES}
        assert mk <= set(p1["stats"]) and all(p1["stats"][k]["n"] > 0 for k in mk)
        rc2, _ = run_c(e, "L2", D("2027-01-01"), D("2026-12-31"), **cand2_kw(e, cp, cp2))
        s = read_json(e.fwd, "point-L2.json")["stats"]
        assert abs(s["S_base|all"]["alpha_k"] - 0.020 * 0.04) < 1e-12 and s["S_base|all"]["alpha_k"] not in (0.025 * 0.04, 0.03 * 0.04)       # C1: S_base 0,020, не 0,025
        assert abs(s["L_mk2|all"]["alpha_k"] - 0.005 * 0.04) < 1e-12 and abs(s["L_n24|all"]["alpha_k"] - 0.005 * 0.04) < 1e-12 and s["L_mk2|crypto"]["alpha_k"] == 0.0
        assert read_json(e.fwd, "point-L2.json")["prefix"]["diffs"]["L_mk2|all"] == 0


def test_missing_candidate2_stops_the_read_and_no_candidate2_keeps_alpha_without_reading_l_mk2():
    with strict(), strict2(), tmp() as t:
        e, c, cp = cand_env(t)                                                                     # tk641 рядом нет
        prepare(e, "L1", D("2026-11-30"))
        r = N24Runner(D("2026-11-30"))
        go = lambda **kw: fw.run_point(e.args("L1", **cand2_kw(e, cp, cp, **kw)), runner=r, today=D("2026-12-01"), repo=e.root)  # noqa: E731
        expect_guard(lambda: go(), "нет файла кандидата")
        expect_guard(lambda: go(no_candidate2=True), "нет файла кандидата")                        # флаг файл не заменяет
        assert r.calls == 0 and not os.path.exists(os.path.join(e.fwd, "READ-L1.started"))
    with strict(), strict2(), tmp() as t:
        e, c, cp, c2, cp2 = cand2_env(t)
        rc, r = run_c(e, "L1", D("2026-12-01"), D("2026-11-30"), **cand2_kw(e, cp, cp2, no_candidate2=True))
        marker = read_json(e.fwd, "READ-L1.started")
        assert rc == 0 and marker["candidate2_read"] is False and "кандидат 2 не читан" in marker["candidate2_note"] and "0.02" in marker["candidate2_note"]
        p1 = read_json(e.fwd, "point-L1.json")
        assert not any(k.startswith("L_mk2|") for k in p1["stats"]) and "кандидат 2 не читан" in fw.render(p1)
        rc2, _ = run_c(e, "L2", D("2027-01-01"), D("2026-12-31"), **cand2_kw(e, cp, cp2, no_candidate2=True))
        s = read_json(e.fwd, "point-L2.json")["stats"]
        assert abs(s["S_base|all"]["alpha_k"] - 0.020 * 0.04) < 1e-12 and "L_mk2|all" not in s


def test_plan_prints_the_candidate2_alpha():
    with tmp() as t:
        e, c, cp, c2, cp2 = cand2_env(t)
        out = subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(HERE, "..", "tk616_forward.py"), "plan", "--freeze", e.freeze, "--root", e.fwd], capture_output=True, text=True,
                             encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
        assert out.returncode == 0 and "tk641-frozen.json (tk641-frozen-1)" in out.stdout and "'L_mk2': 0.005" in out.stdout and "α(S_base) 0.0008" in out.stdout, out.stdout + out.stderr


def test_replay_mk2_compares_outputs_with_g626c_byte_for_byte():
    old_files = fw.FILES_641
    fw.FILES_641 = ("long-trades.csv",)                                                            # подменный движок пишет только trades.csv
    try:
        with tmp() as t:
            e, c, cp, c2, cp2 = cand2_env(t)
            root = os.path.join(t, "r")
            os.makedirs(root)
            write_list(os.path.join(root, "symbols-all.txt"), COINS)
            mk_tree(os.path.join(root, "win"), "", rf.WINDOWS, D("2026-11-13"), coins=COINS)
            ref = os.path.join(root, "ref")
            os.makedirs(ref)
            vid = c2["cells"]["L_mk2"]["vid"]

            def replay(name, **kw):
                a = dict(freeze=e.freeze, freeze_commit="c0ffee", freeze_sha256=fw.sha256_text(e.freeze), bin=e.bin, threads=1, tick_store="", sig_cache="", **cand2_kw(e, cp, cp2),
                         replay_root=os.path.join(root, name), windows_root=os.path.join(root, "win"), symbols_file=os.path.join(root, "symbols-all.txt"), ref_dir=ref, out="")
                a.update(kw)
                return fw.replay_mk2(argparse.Namespace(**a), runner=FakeRunner(D("2026-11-13")), repo=e.root)

            assert replay("rp1") == 3                                                              # эталона нет: не пройдено
            res = read_json(root, "rp1", "gate-641c.json")
            assert res["ok"] is False and res["vid"] == vid and all(v["files"]["long-trades.csv"]["ref_sha256"] is None for v in res["windows"].values())
            for w in rf.WINDOWS:                                                                    # эталон = выходам
                os.makedirs(os.path.join(ref, w.name, vid))
                shutil.copy(os.path.join(root, "rp1", "out", "replay", w.name, vid, "long-trades.csv"), os.path.join(ref, w.name, vid, "long-trades.csv"))
            assert replay("rp2") == 0 and read_json(root, "rp2", "gate-641c.json")["ok"] is True
            open(os.path.join(ref, "wb", vid, "long-trades.csv"), "a").write("x\n")                  # одно различие — не пройдено
            assert replay("rp3") == 3
            r3 = read_json(root, "rp3", "gate-641c.json")
            assert r3["windows"]["wb"]["equal"] is False and r3["windows"]["wa"]["equal"] and r3["windows"]["wc"]["equal"]
            expect_guard(lambda: replay("rp4", no_candidate2=True), "без --no-candidate2")
            expect_guard(lambda: replay("rp5", ref_dir=os.path.join(root, "нет")), "--ref-dir")
            late = os.path.join(root, "win", "wc", "klines", COINS[0], "%s-klines-2026-11-14.zip" % COINS[0])
            open(late, "w").close()
            expect_guard(lambda: replay("rp6"), "позже 2026-11-13")
    finally:
        fw.FILES_641 = old_files


def main():
    fails = 0
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    for n, f in tests:
        try:
            f()
            print("ok   ", n)
        except Exception as e:  # noqa: BLE001
            fails += 1
            print("FAIL ", n, "-", type(e).__name__, e)
            tb = traceback.extract_tb(e.__traceback__)
            print("       %s:%d  %s" % (os.path.basename(tb[-1].filename), tb[-1].lineno, tb[-1].line))
    print("%d тестов, %d провалов" % (len(tests), fails))
    return 1 if fails else 0


if __name__ == "__main__":
    sys.exit(main())
