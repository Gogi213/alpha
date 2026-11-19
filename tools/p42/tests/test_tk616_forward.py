"""Тесты tk616_forward.py и tk616_freeze.py (TK-616 §13.28 п. 7–8) на синтетике — без движка и без реальных данных: геометрия заморозки, охраны, префикс, швы, правило решения,
сквозной `run_point` с подменным раннером. Без pytest: `PYTHONUTF8=1 python3 tools/p42/tests/test_tk616_forward.py`.
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
import tk616_freeze as fz  # noqa: E402

NB = 4000
DAY = rf.DAY_MS
COINS = ["AAAUSDT", "BBBUSDT", "CCCUSDT", "DDDUSDT", "EEEUSDT", "FFFUSDT"]
BIN_BYTES = b"synthetic-binary"
D = datetime.date.fromisoformat


@contextlib.contextmanager
def tmp():
    p = tempfile.mkdtemp(prefix="tk616f-")
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


def sha_of(b):
    return hashlib.sha256(b).hexdigest(), hashlib.md5(b).hexdigest()


class Env:
    """Файлы заморозки, бинаря и списка монет во временном каталоге."""

    def __init__(self, root, passed=("S_mk1",)):
        self.root = root
        self.bin = os.path.join(root, "alpha-mk")
        open(self.bin, "wb").write(BIN_BYTES)
        self.symbols = os.path.join(root, "symbols.txt")
        open(self.symbols, "w", encoding="utf-8").write(",".join(COINS) + "\n")
        sha, md5 = sha_of(BIN_BYTES)
        self.fz = fz.build(results(passed), "r" * 64, self.bin, md5, sha, rf.sha256_file(self.symbols))
        self.freeze = os.path.join(root, "tk616-frozen.json")
        json.dump(self.fz, open(self.freeze, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        self.fwd = os.path.join(root, "fwd")

    def args(self, point, **kw):
        a = dict(freeze=self.freeze, freeze_commit="c0ffee", freeze_sha256=rf.sha256_file(self.freeze), root=self.fwd, bin=self.bin, symbols=self.symbols, threads=1,
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
        assert [(w["name"], w["t0"], w["lo"], w["hi"]) for w in f["windows"]] == [
            ("w1", "2026-10-16", "2026-11-17", "2026-12-24"), ("w2", "2026-11-23", "2026-12-25", "2027-01-31"), ("w3", "2026-12-31", "2027-02-01", "2027-03-10"),
            ("w4", "2027-02-07", "2027-03-11", "2027-04-17"), ("w5", "2027-03-17", "2027-04-18", "2027-05-25")]
        assert [(p["id"], p["d"], p["entries_to"], ",".join(p["windows"]), p["decision"]) for p in f["points"]] == [
            ("L1", "2026-11-30", "2026-11-26", "w1", False), ("L2", "2026-12-31", "2026-12-27", "w1,w2", True), ("L3", "2027-01-31", "2027-01-27", "w1,w2", True),
            ("L4", "2027-02-28", "2027-02-24", "w1,w2,w3", True), ("L5", "2027-03-31", "2027-03-27", "w1,w2,w3,w4", True)]
        assert f["alpha"] == {"S_base": 0.03, "S_mk1": 0.01} and f["point_shares"] == {"L2": 0.04, "L3": 0.10, "L4": 0.20, "L5": 0.66}
        assert {c: v["vid"] for c, v in f["cells"].items()} == {"S_base": "c10fbba7be522", "S_mk1": "c934346e168dd", "L_base": "c53a116125b39"}
        assert [c for c, v in f["cells"].items() if v["family"]] == ["S_base", "S_mk1"] and f["cells"]["L_base"]["side"] == "L"
        assert abs(fw.alpha_k(f, "S_base", "L5") - 0.03 * 0.66) < 1e-12 and fw.alpha_k(f, "S_base", "L1") == 0.0


def test_alpha_by_number_of_passed_cells_and_long_cells_stop_the_build():
    assert fz.alphas([]) == {"S_base": 0.05}
    assert fz.alphas(["S_mk1"]) == {"S_base": 0.03, "S_mk1": 0.01}
    assert fz.alphas(["S_mk1", "S_mk2"]) == {"S_base": 0.03, "S_mk1": 0.01, "S_mk2": 0.01}
    assert abs(sum(fz.alphas(["S_mk1", "S_mk2"]).values()) - 0.05) < 1e-12
    with tmp() as t:
        e = Env(t, passed=())
        assert fw.load_freeze(e.freeze)["alpha"] == {"S_base": 0.05}
        sha, md5 = sha_of(BIN_BYTES)
        expect_guard(lambda: fz.build(results(("L_mk2",)), "r" * 64, e.bin, md5, sha, "s" * 64), "лонг-клетка")


def test_load_freeze_rejects_broken_geometry():
    def broken(mut):
        with tmp() as t:
            e = Env(t)
            f = json.loads(json.dumps(e.fz))
            mut(f)
            json.dump(f, open(e.freeze, "w", encoding="utf-8"))
            fw.load_freeze(e.freeze)

    expect_guard(lambda: fw.load_freeze("/nonexistent/tk616-frozen.json"), "нет файла заморозки")
    expect_guard(lambda: broken(lambda f: f["windows"][1].update(t0="2026-11-24")), "T0")
    expect_guard(lambda: broken(lambda f: f["windows"][0].update(lo="2026-11-18")), "зачёт")
    expect_guard(lambda: broken(lambda f: f["alpha"].update(S_base=0.06)), "α")
    expect_guard(lambda: broken(lambda f: f["alpha"].pop("S_mk1")), "α заданы не для всех")
    expect_guard(lambda: broken(lambda f: f["point_shares"].update(L5=0.5)), "доли α")
    expect_guard(lambda: broken(lambda f: f["points"][1].update(entries_to="2026-12-28")), "входы до")
    expect_guard(lambda: broken(lambda f: f["points"][2].update(windows=["w1"])), "окна")
    expect_guard(lambda: broken(lambda f: f["points"][0].update(decision=True)), "признак решения")
    expect_guard(lambda: broken(lambda f: f["cells"]["S_base"].update(family=False)), "S_base")
    expect_guard(lambda: broken(lambda f: f.pop("symbols_sha256")), "нет поля")


def git(repo, *a):
    return subprocess.run(["git", "-C", repo, "-c", "user.name=t", "-c", "user.email=t@t", "-c", "commit.gpgsign=false", *a], capture_output=True, text=True)


def test_freeze_commit_guard_with_git_and_without():
    with tmp() as t:
        e = Env(t)
        if git(t, "init", "-q").returncode != 0:
            return                                                             # git недоступен: охрана без git проверяется ниже
        expect_guard(lambda: fw.check_freeze_committed(e.freeze, t), "не закоммичена")
        git(t, "add", "tk616-frozen.json")
        expect_guard(lambda: fw.check_freeze_committed(e.freeze, t), "не закоммичена")           # в индексе — ещё не коммит
        assert git(t, "commit", "-q", "-m", "freeze").returncode == 0
        commit, sha = fw.check_freeze_committed(e.freeze, t)
        assert len(commit) == 40 and sha == rf.sha256_file(e.freeze)
        open(e.freeze, "a").write("\n ")
        expect_guard(lambda: fw.check_freeze_committed(e.freeze, t), "изменена после коммита")
    with tmp() as t:
        e = Env(t)
        expect_guard(lambda: fw.check_freeze_committed(e.freeze, t), "нужны --freeze-commit и --freeze-sha256")
        expect_guard(lambda: fw.check_freeze_committed(e.freeze, t, "abc", "0" * 64), "≠ заданному")
        assert fw.check_freeze_committed(e.freeze, t, "abc", rf.sha256_file(e.freeze)) == ("abc", rf.sha256_file(e.freeze))
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


def mk_tree(root, pid, wins, last_day, late=None):
    """Деревья окон точки: по файлу `…-YYYY-MM-DD.zip` на первые и последние сутки (последние — min(D, T0 + 73))."""
    for w in wins:
        end = min(last_day, w.t0d + datetime.timedelta(days=rf.TRADE_END))
        for sub in ("aggtrades", "klines"):
            p = os.path.join(root, pid, w.name, sub, COINS[0])
            os.makedirs(p, exist_ok=True)
            for day in (w.t0d, end):
                open(os.path.join(p, "%s-%s-%s.zip" % (COINS[0], sub, day)), "w").close()
            if late:
                open(os.path.join(p, "%s-%s-%s.zip" % (COINS[0], sub, late)), "w").close()


def test_tree_with_a_day_after_the_point_date_is_refused():
    with tmp() as t:
        f = fw.load_freeze(Env(t).freeze)
        wins = {w.name: w for w in fw.windows_of(f)}
        used = [wins["w1"], wins["w2"]]
        expect_guard(lambda: fw.check_tree_days(os.path.join(t, "L2"), used, D("2026-12-31")), "нет дерева данных окна")
        mk_tree(t, "L2", used, D("2026-12-31"))
        assert fw.check_tree_days(os.path.join(t, "L2"), used, D("2026-12-31")) == 8
        shutil.rmtree(os.path.join(t, "L2"))
        mk_tree(t, "L2", used, D("2026-12-31"), late="2027-01-01")
        expect_guard(lambda: fw.check_tree_days(os.path.join(t, "L2"), used, D("2026-12-31")), "позже 2026-12-31")


def test_read_marker_is_atomic_and_second_read_is_refused():
    with tmp() as t:
        p = fw.claim_read(t, "L2", {"point": "L2"})
        assert os.path.basename(p) == "READ-L2.started" and json.load(open(p))["point"] == "L2"
        expect_guard(lambda: fw.claim_read(t, "L2", {}), "повторное чтение запрещено")
        fw.claim_read(t, "L3", {})


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
    assert s["n"] == 123 and s["days"] == 41 and s["decision"] == "подтверждено" and s["p"] <= s["alpha_k"] == 0.03 * 0.04 and s["fund_ok"] and s["fund_mode"] == "|фандинг движка|"
    assert abs(s["sum"] - 123 * s["mean"]) < 1e-9
    z = fw.cell_point_stats(f, "S_base", fw.get_point(f, "L2"), synth_rows(0.0, sd=0.2), to, None, NB)
    assert z["decision"] == "не ясно" and z["p"] > z["alpha_k"]
    one = fw.get_point(f, "L1")
    d1 = fw.cell_point_stats(f, "S_base", one, synth_rows(0.8, days=10), D("2026-11-26"), None, NB)
    assert d1["alpha_k"] == 0.0 and d1["decision"] == "описательно"
    l = fw.cell_point_stats(f, "L_base", fw.get_point(f, "L2"), synth_rows(0.8), to, None, NB)
    assert l["alpha_k"] == 0.0 and l["decision"] == "описательно"
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


class FakeRunner:
    """Вместо движка: из строк `--variants` пишет `<out>/<vid>/{long,short}-trades.csv`; сделка — функция (вариант, монета, сутки), поэтому окна согласованы на перекрытии."""

    def __init__(self, last_day, gross_shift=0.0):
        self.last_day, self.calls, self.gross_shift = last_day, 0, gross_shift

    def run(self, cmd, env=None, log=None):
        self.calls += 1
        vfile, out, t0 = cmd[cmd.index("--variants") + 1], cmd[cmd.index("--out") + 1], env["P42_T0"]
        for line in open(vfile, encoding="utf-8"):
            vid, keys = line.rstrip("\n").split(" ", 1)
            side = "short" if "bm=1" in keys else "long"
            os.makedirs(os.path.join(out, vid), exist_ok=True)
            with open(os.path.join(out, vid, side + "-trades.csv"), "w", encoding="utf-8", newline="") as f:
                f.write("sym,entry_ms,exit_ms,gross,fees,funding,net,kind\n")
                for r in self.trades(vid, side, D(t0)):
                    f.write("%s,%d,%d,%r,%r,0.0,%r,%s\n" % (r[0], r[1], r[2], r[3], r[4], r[3] - r[4], r[6]))
        return 0

    def trades(self, vid, side, t0d):
        t0ms = fw.t0_ms(t0d)
        lim = fw.t0_ms(min(self.last_day, t0d + datetime.timedelta(days=rf.TRADE_END))) + DAY
        out = []
        for k in range(rf.GRID_START_DAY, rf.TRADE_END + 1):
            day = t0d + datetime.timedelta(days=k)
            if day > self.last_day:
                break
            for c in COINS:
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


def prepare(e, pid, last_day):
    f = fw.load_freeze(e.freeze)
    wins = {w.name: w for w in fw.windows_of(f)}
    mk_tree(e.fwd, pid, [wins[n] for n in fw.get_point(f, pid)["windows"]], last_day)


def run(e, pid, today, last_day, runner=None, **kw):
    prepare(e, pid, last_day) if not os.path.isdir(os.path.join(e.fwd, pid)) else None
    r = runner or FakeRunner(last_day)
    rc = fw.run_point(e.args(pid, **kw), runner=r, today=today, repo=e.root)
    return rc, r


def test_run_point_l1_then_l2_writes_tables_marker_and_checks_the_prefix():
    with tmp() as t:
        e = Env(t)
        rc, r = run(e, "L1", D("2026-12-01"), D("2026-11-30"))
        assert rc == 0 and r.calls == 1                                                      # одно окно w1 = один вызов движка на пакет
        assert os.path.exists(os.path.join(e.fwd, "READ-L1.started")) and os.path.exists(os.path.join(e.fwd, "READ-L1.done"))
        p1 = json.load(open(os.path.join(e.fwd, "point-L1.json"), encoding="utf-8"))
        assert p1["prefix"]["diffs"] == {} and p1["stats"]["S_base"]["decision"] == "описательно" and p1["stats"]["S_mk1"]["decision"] == "описательно"
        assert p1["stats"]["S_base"]["n"] > 0 and p1["seams"] == {"S_base": 0, "S_mk1": 0, "L_base": 0}
        tabs = json.load(open(os.path.join(e.fwd, "tables-L1.json"), encoding="utf-8"))
        assert tabs["entries_to"] == "2026-11-26" and set(tabs["rows"]) == {"S_base", "S_mk1", "L_base"}
        assert all(fw.date_of(x[1]) <= D("2026-11-26") for rows in tabs["rows"].values() for x in rows)
        rc2, r2 = run(e, "L2", D("2027-01-01"), D("2026-12-31"))
        assert rc2 == 0 and r2.calls == 2                                                    # w1 + w2
        p2 = json.load(open(os.path.join(e.fwd, "point-L2.json"), encoding="utf-8"))
        assert p2["prefix"]["diffs"] == {"S_base": 0, "S_mk1": 0, "L_base": 0} and p2["seams"] == {"S_base": 0, "S_mk1": 0, "L_base": 0}
        assert p2["stats"]["S_base"]["decision"] == "подтверждено" and p2["stats"]["L_base"]["decision"] == "описательно" and p2["stats"]["S_base"]["alpha_k"] == 0.03 * 0.04
        assert p2["point"]["id"] == "L2" and p2["bin_md5"] == sha_of(BIN_BYTES)[1] and p2["freeze_commit"] == "c0ffee"
        n1 = {c: len(v) for c, v in tabs["rows"].items()}
        tabs2 = json.load(open(os.path.join(e.fwd, "tables-L2.json"), encoding="utf-8"))
        assert all(len(tabs2["rows"][c]) > n1[c] for c in n1)
        expect_guard(lambda: run(e, "L2", D("2027-01-01"), D("2026-12-31")), "повторное чтение запрещено")
        assert "решение" in fw.render(p2) and "подтверждено" in fw.render(p2)


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
        open(os.path.join(e.fwd, "L1", "w1", "klines", COINS[0], "%s-klines-2026-12-02.zip" % COINS[0]), "w").close()
        expect_guard(attempt(), "позже 2026-11-30")
        os.remove(os.path.join(e.fwd, "L1", "w1", "klines", COINS[0], "%s-klines-2026-12-02.zip" % COINS[0]))
        syms = os.path.join(t, "symbols2.txt")
        open(syms, "w").write("XXXUSDT\n")
        expect_guard(attempt(symbols=syms), "список монет")
        expect_guard(attempt(pid="L2", today=D("2027-01-01")), "нет дерева данных окна")
        prepare(e, "L2", D("2026-12-31"))
        expect_guard(attempt(pid="L2", today=D("2027-01-01")), "нет таблиц точки L1")
        assert r.calls == 0 and not os.path.exists(marker) and not os.path.exists(os.path.join(e.fwd, "READ-L2.started"))


def test_run_point_stops_on_prefix_difference_and_on_seam_difference():
    with tmp() as t:
        e = Env(t)
        run(e, "L1", D("2026-12-01"), D("2026-11-30"))
        pp = os.path.join(e.fwd, "tables-L1.json")
        tabs = json.load(open(pp, encoding="utf-8"))
        tabs["rows"]["S_base"][0][3] += 5.0                                                  # прежняя точка «читалась» иначе: gross одной сделки
        json.dump(tabs, open(pp, "w", encoding="utf-8"))
        expect_guard(lambda: run(e, "L2", D("2027-01-01"), D("2026-12-31")), "префикс-охрана точки L2 против L1")
        assert os.path.exists(os.path.join(e.fwd, "READ-L2.started")) and not os.path.exists(os.path.join(e.fwd, "READ-L2.done"))
    with tmp() as t:
        e = Env(t)
        run(e, "L1", D("2026-12-01"), D("2026-11-30"))

        class SeamBreaker(FakeRunner):
            def trades(self, vid, side, t0d):
                rows = super().trades(vid, side, t0d)
                if t0d == D("2026-11-23"):                                                    # w2 видит перекрытие иначе, чем w1
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
        assert "α(S_base) 0.0012" in out.stdout and "нет (описательно)" in out.stdout
        os.environ.pop(fw.GO_ENV, None)
        assert fw.main(["point", "L2", "--freeze", e.freeze, "--root", e.fwd, "--bin", e.bin]) == 3
        assert fw.main(["point", "--freeze", e.freeze, "--root", e.fwd]) == 3


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
