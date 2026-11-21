#!/usr/bin/env python3
"""TK-670 (§13.70): расширение `tk616_forward.py` для кандидата вперёд `D_S` (E4 / D шорт; звено цепочки tk626 → tk638 → tk641 → tk670). Синтетика, как `test_tk616_forward.py`
(его заготовки — среда `Env`, кандидаты tk638 / tk641, подменный движок — берутся оттуда). Запуск: `python3 tools/p42/tests/test_tk670.py` (на srv)."""
from __future__ import annotations

import argparse
import contextlib
import datetime
import json
import os
import shutil
import subprocess
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import retitr_full as rf  # noqa: E402
import test_tk616_forward as T  # noqa: E402
import tk616_forward as fw  # noqa: E402

fw.REQUIRE_CANDIDATE3 = False                         # прежние тесты — без `tk670-frozen.json`; охрана «нет кандидата 3 — остановка» проверяется в `strict3()`
DS_BYTES = b"synthetic-ds-binary"


@contextlib.contextmanager
def strict3():
    fw.REQUIRE_CANDIDATE3 = True
    try:
        yield
    finally:
        fw.REQUIRE_CANDIDATE3 = False


def make_candidate3(e, c2, cp, cp2, mutate=None):
    """Кандидат 3 `tk670-frozen-1` против заморозки, tk638 и tk641 среды `e`: α `S_mk1` 0,010 → 0,005 + `D_S` 0,005, клетка = `S_base` + `lx=1 tf=60`; пишется рядом с заморозкой."""
    f = json.loads(json.dumps(e.fz))
    base = f["cells"]["S_base"]
    keys = fw.ds_keys(base["keys"])
    alpha = dict(c2["alpha"])
    alpha["S_mk1"] = round(alpha["S_mk1"] - 0.005, 10)
    alpha["D_S"] = 0.005
    sha, md5 = T.sha_of(DS_BYTES)
    c3 = {"version": "tk670-frozen-1", "base_freeze": {"version": "tk626-frozen-1", "sha256": fw.sha256_text(e.freeze)},
          "prev_candidate": {"version": "tk638-frozen-1", "sha256": fw.sha256_text(cp)}, "prev_candidate2": {"version": "tk641-frozen-1", "sha256": fw.sha256_text(cp2)},
          "binary": f["binary"], "ds_binary": {"md5": md5, "sha256": sha, "path": "/data/p42-tk670/bin/alpha-lx.test"}, "universe": f["universe"], "point_shares": f["point_shares"],
          "points": f["points"], "windows": f["windows"],
          "cells": {"D_S": {"side": "S", "base_keys": base["keys"], "base_vid": base["vid"], "keys": keys, "vid": rf.Req("S", keys, False).vid, "family": False, "candidate": True}},
          "alpha": alpha, "alpha_k": {k: {p: v * s for p, s in f["point_shares"].items()} for k, v in alpha.items()}}
    if mutate:
        mutate(c3)
    path = fw.candidate3_path(e.freeze)
    json.dump(c3, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    e.ds = os.path.join(e.root, "alpha-lx")
    open(e.ds, "wb").write(DS_BYTES)
    return c3, path


def cand3_env(t, mutate=None):
    e, c, cp, c2, cp2 = T.cand2_env(t)
    c3, cp3 = make_candidate3(e, c2, cp, cp2, mutate)
    return e, c, cp, c2, cp2, c3, cp3


def cand3_kw(e, cp, cp2, cp3, **kw):
    out = T.cand2_kw(e, cp, cp2, candidate3="", candidate3_commit="c0ffee", candidate3_sha256=fw.sha256_text(cp3), ds_bin=e.ds)
    out.update(kw)
    return out


class Rec(T.FakeRunner):
    """Подменный движок с записью бинарей запусков (`cmd[0]`) и строк `--variants`."""

    def __init__(self, last_day):
        super().__init__(last_day)
        self.bins, self.vids = [], []

    def run(self, cmd, env=None, log=None):
        self.bins.append(cmd[0])
        self.vids.append([x.split(" ", 1)[0] for x in open(cmd[cmd.index("--variants") + 1], encoding="utf-8")])
        return super().run(cmd, env=env, log=log)


def prep(e, pid, last_day):
    """Деревья точки + klines суток ПЕРЕД первыми сутками листинга прироста (иначе первые сутки новой монеты — пропуск: уровень без вчерашних баров, консервативно, и на малой синтетике это > 1 %)."""
    T.prepare(e, pid, last_day)
    f = fw.load_freeze(e.freeze)
    for w in fw.windows_of(f):
        if w.name not in fw.get_point(f, pid)["windows"]:
            continue
        for c, first in T.FIRST.items():
            p = os.path.join(e.fwd, pid, w.name, "klines", c)
            day = T.D(first) - datetime.timedelta(days=1)
            if os.path.isdir(p) and day >= w.t0d:
                open(os.path.join(p, "%s-klines-%s.zip" % (c, day)), "w").close()


def test_candidate3_real_files_chain_alpha_and_cell():
    """Реальная цепочка репозитория tk626 → tk638 → tk641 → tk670: α `S_base` 0,020, `S_mk1` 0,005, `D_S` 0,005 (Σ 0,05); `D_S` = `S_base` + `lx=1 tf=60` без `lw`; файлы прежних звеньев не меняются."""
    fz0 = fw.load_freeze(fw.FREEZE)
    cp, cp2, cp3 = fw.candidate_path(fw.FREEZE), fw.candidate2_path(fw.FREEZE), fw.candidate3_path(fw.FREEZE)
    assert os.path.basename(cp3) == "tk670-frozen.json"
    c = fw.load_candidate(cp, fz0, fw.FREEZE)
    c2 = fw.load_candidate2(cp2, fz0, fw.FREEZE, c, cp)
    c3 = fw.load_candidate3(cp3, fz0, fw.FREEZE, c, cp, c2, cp2)
    fz = fw.apply_candidate3(fw.apply_candidate2(fw.apply_candidate(fz0, c), c2, True), c3)
    assert fz["alpha"] == {"S_base": 0.02, "S_mk1": 0.005, "L_base": 0.01, "L_n24": 0.005, "L_mk2": 0.005, "D_S": 0.005} and abs(sum(fz["alpha"].values()) - 0.05) <= 1e-12
    assert c2["alpha"]["S_mk1"] == 0.01 and c2["alpha"]["S_base"] == 0.02 and "D_S" not in c2["alpha"]            # tk641 не меняется
    cell = c3["cells"]["D_S"]
    assert cell["keys"] == fz0["cells"]["S_base"]["keys"].replace(" t24=12", " lx=1 tf=60 t24=12") and "lw" not in cell["keys"] and cell["vid"] == rf.Req("S", cell["keys"], False).vid
    assert "D_S" not in fz["cells"] and set(fz["cells"]) == set(fz0["cells"]) | {"L_mk2"}              # клетка D_S — отдельный прогон (L_mk2 — клетка пакета, tk641)
    assert abs(fw.alpha_k(fz, "D_S", "L5") - 0.0033) < 1e-12 and abs(fw.alpha_k(fz, "D_S", "L2") - 0.0002) < 1e-12 and abs(fw.alpha_k(fz, "S_mk1", "L5") - 0.0033) < 1e-12
    assert abs(fw.alpha_k(fz, "S_base", "L5") - 0.0132) < 1e-12
    assert len(c3["ds_binary"]["md5"]) == 32 and len(c3["ds_binary"]["sha256"]) == 64 and c3["ds_binary"]["source_commit"] == "1691d6bbf"
    assert c3["gates"]["G-670a"]["verdict"] == "ПРОЙДЕНЫ"


def test_candidate3_guards_every_rule():
    def bad(needle, mutate):
        with T.tmp() as t:
            e, c, cp, c2, cp2, c3, cp3 = cand3_env(t, mutate)
            T.expect_guard(lambda: fw.load_candidate3(cp3, fw.load_freeze(e.freeze), e.freeze, c, cp, c2, cp2), needle)

    with T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3 = cand3_env(t)
        fz = fw.load_freeze(e.freeze)
        assert fw.load_candidate3(cp3, fz, e.freeze, c, cp, c2, cp2)["alpha"]["S_mk1"] == 0.005
        T.expect_guard(lambda: fw.load_candidate3(cp3, fz, e.freeze, c, cp, None, cp2), "цепочка tk626 → tk638 → tk641 → tk670 разорвана")
        T.expect_guard(lambda: fw.load_candidate3(os.path.join(t, "нет.json"), fz, e.freeze, c, cp, c2, cp2), "нет файла кандидата")
        open(cp2, "a").write("\n ")                                                                    # tk641 изменён после записи tk670
        T.expect_guard(lambda: fw.load_candidate3(cp3, fz, e.freeze, c, cp, c2, cp2), "записан против другого файла")
    bad("версия кандидата 3", lambda x: x.update(version="tk670-frozen-2"))
    bad("записан против другого файла", lambda x: x["prev_candidate"].update(sha256="0" * 64))
    bad("записан против другого файла", lambda x: x["prev_candidate2"].update(version="tk641-frozen-2"))
    bad("записан против другого файла", lambda x: x["base_freeze"].update(sha256="0" * 64))
    bad("поле 'binary'", lambda x: x["binary"].update(md5="0" * 32))
    bad("поле 'points'", lambda x: x["points"][1].update(d="2026-12-30"))
    bad("нет md5 / sha256 / пути `ds_binary`", lambda x: x["ds_binary"].pop("sha256"))
    bad("клетки", lambda x: x["cells"].update(X=dict(x["cells"]["D_S"])))
    bad("клетка D_S", lambda x: x["cells"]["D_S"].update(keys=x["cells"]["D_S"]["keys"].replace("lx=1", "lx=2")))
    bad("клетка D_S", lambda x: x["cells"]["D_S"].update(keys=x["cells"]["D_S"]["keys"] + " lw=3"))
    bad("клетка D_S", lambda x: x["cells"]["D_S"].update(family=True))
    bad("vid", lambda x: x["cells"]["D_S"].update(vid="cf280a1e8fc7d"))
    bad("α кандидата 3 заданы", lambda x: x["alpha"].pop("L_n24"))
    bad("сумма α кандидата 3", lambda x: x["alpha"].update(D_S=0.006))
    bad("α(D_S)", lambda x: x["alpha"].update(D_S=0.0, S_mk1=0.01))
    bad("берёт α ровно с одной", lambda x: x["alpha"].update(S_mk1=0.0075, L_base=0.0075))             # тронуты обе доли: донор ровно один
    bad("берёт α ровно с одной", lambda x: x["alpha"].update(L_n24=0.0, D_S=0.005, S_mk1=0.01))          # донор вне S_mk1 / L_base
    bad("берёт α ровно с одной", lambda x: x["alpha"].update(S_mk1=0.003, D_S=0.007))                    # у донора остаётся < 0,005
    bad("берёт α ровно с одной", lambda x: x["alpha"].update(S_base=0.015, D_S=0.01))                    # α(S_base) ниже пола 0,020 (В-167): S_base — не донор
    bad("α по точкам", lambda x: x["alpha_k"]["D_S"].update(L5=0.0165))
    bad("α по точкам", lambda x: x["alpha_k"].pop("D_S"))


def test_run_point_with_candidate3_reads_ds_with_its_binary_alpha_and_joint_description():
    with T.strict(), T.strict2(), strict3(), T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3 = cand3_env(t)
        r = Rec(T.D("2026-11-30"))
        prep(e, "L1", T.D("2026-11-30"))
        rc, r = T.run_c(e, "L1", T.D("2026-12-01"), T.D("2026-11-30"), runner=r, **cand3_kw(e, cp, cp2, cp3))
        assert rc == 0
        marker = T.read_json(e.fwd, "READ-L1.started")
        assert marker["candidate3"] == "D_S" and marker["candidate3_read"] is True and marker["candidate3_sha256"] == fw.sha256_text(cp3) and marker["candidate2"] == "L_mk2"
        assert e.ds in r.bins and e.bin in r.bins                                                           # D_S — бинарём `ds_binary`, клетки пакета — замороженным бинарём
        ds_vid = c3["cells"]["D_S"]["vid"]
        assert all((ds_vid in v) == (b == e.ds) for b, v in zip(r.bins, r.vids)) and any(ds_vid in v for v in r.vids)       # ключ `lx` — только в ds-бинаре
        p1 = T.read_json(e.fwd, "point-L1.json")
        ds = {"D_S|%s" % u for u in fw.UNI_NAMES}
        assert ds <= set(p1["stats"]) and all(p1["stats"][k]["n"] > 0 for k in ds) and p1["stats"]["D_S|all"]["decision"] != "подтверждено"
        assert p1["ds"]["all"]["status"] == "read" and p1["ds"]["all"]["missing_share"] == 0.0 and T.read_json(e.fwd, "READ-L1.ds.json")["all"]["status"] == "read"
        j = p1["joint"]
        assert j["n_a"] == p1["stats"]["S_base|all"]["n"] and j["n_b"] == p1["stats"]["D_S|all"]["n"] and j["n"] == j["n_a"] + j["n_b"]
        assert abs(j["sum"] - p1["stats"]["S_base|all"]["sum"] - p1["stats"]["D_S|all"]["sum"]) < 1e-9 and "Совместно" in fw.render(p1) and "вердикт" in fw.render(p1)
        prep(e, "L2", T.D("2026-12-31"))
        rc2, _ = T.run_c(e, "L2", T.D("2027-01-01"), T.D("2026-12-31"), runner=Rec(T.D("2026-12-31")), **cand3_kw(e, cp, cp2, cp3))
        p2 = T.read_json(e.fwd, "point-L2.json")
        s = p2["stats"]
        assert abs(s["D_S|all"]["alpha_k"] - 0.005 * 0.04) < 1e-12 and s["D_S|crypto"]["alpha_k"] == 0.0                       # C1: α D_S из tk670, решающая вселенная — all
        assert abs(s["S_mk1|all"]["alpha_k"] - 0.005 * 0.04) < 1e-12 and abs(s["S_mk1|all"]["alpha_k"] - 0.010 * 0.04) > 1e-6  # α S_mk1 урезана (0,010 → 0,005)
        assert abs(s["S_base|all"]["alpha_k"] - 0.020 * 0.04) < 1e-12 and p2["prefix"]["diffs"]["D_S|all"] == 0


def test_missing_candidate3_stops_the_read_and_no_candidate3_keeps_alpha_without_reading_ds():
    with T.strict(), T.strict2(), strict3(), T.tmp() as t:
        e, c, cp, c2, cp2 = T.cand2_env(t)                                                              # tk670 рядом нет
        e.ds = os.path.join(t, "alpha-lx")
        open(e.ds, "wb").write(DS_BYTES)
        T.prepare(e, "L1", T.D("2026-11-30"))
        r = Rec(T.D("2026-11-30"))
        go = lambda **kw: fw.run_point(e.args("L1", **T.cand2_kw(e, cp, cp2, candidate3="", candidate3_commit="c0ffee", candidate3_sha256="0" * 64, ds_bin=e.ds, **kw)), runner=r,  # noqa: E731
                                       today=T.D("2026-12-01"), repo=e.root)
        T.expect_guard(lambda: go(), "нет файла кандидата")
        T.expect_guard(lambda: go(no_candidate3=True), "нет файла кандидата")                           # флаг файл не заменяет
        assert r.bins == [] and not os.path.exists(os.path.join(e.fwd, "READ-L1.started"))
    with T.strict(), T.strict2(), strict3(), T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3 = cand3_env(t)
        rc, r = T.run_c(e, "L1", T.D("2026-12-01"), T.D("2026-11-30"), runner=Rec(T.D("2026-11-30")), **cand3_kw(e, cp, cp2, cp3, no_candidate3=True))
        marker = T.read_json(e.fwd, "READ-L1.started")
        assert rc == 0 and marker["candidate3_read"] is False and "кандидат 3 не читан" in marker["candidate3_note"] and e.ds not in r.bins
        p1 = T.read_json(e.fwd, "point-L1.json")
        assert not any(k.startswith("D_S|") for k in p1["stats"]) and p1["joint"] is None and "кандидат 3 не читан" in fw.render(p1)
        rc2, _ = T.run_c(e, "L2", T.D("2027-01-01"), T.D("2026-12-31"), runner=Rec(T.D("2026-12-31")), **cand3_kw(e, cp, cp2, cp3, no_candidate3=True))
        s = T.read_json(e.fwd, "point-L2.json")["stats"]
        assert abs(s["S_mk1|all"]["alpha_k"] - 0.005 * 0.04) < 1e-12 and "D_S|all" not in s


def test_ds_binary_other_than_frozen_is_refused_before_the_marker_and_the_engine():
    with T.strict(), T.strict2(), strict3(), T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3 = cand3_env(t)
        T.prepare(e, "L1", T.D("2026-11-30"))
        r = Rec(T.D("2026-11-30"))
        other = os.path.join(t, "other-lx")
        open(other, "wb").write(b"other")
        go = lambda ds: fw.run_point(e.args("L1", **cand3_kw(e, cp, cp2, cp3, ds_bin=ds)), runner=r, today=T.D("2026-12-01"), repo=e.root)  # noqa: E731
        T.expect_guard(lambda: go(other), "ds-бинарь")
        T.expect_guard(lambda: go(""), "--ds-bin")
        T.expect_guard(lambda: go(os.path.join(t, "нет")), "--ds-bin")
        assert r.bins == [] and not os.path.exists(os.path.join(e.fwd, "READ-L1.started"))


def test_ds_missing_klines_share_over_one_percent_skips_only_ds():
    with T.strict(), T.strict2(), strict3(), T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3 = cand3_env(t)
        prep(e, "L1", T.D("2026-11-30"))
        f = fw.load_freeze(e.freeze)
        wins = [w for w in fw.windows_of(f) if w.name in fw.get_point(f, "L1")["windows"]]
        lohi = lambda w: (max(w.lod, fw.F0), min(w.hid, T.D("2026-11-26")))  # noqa: E731
        pdir = os.path.join(e.fwd, "L1")
        miss0, tot = fw.ds_missing_pairs(pdir, wins, T.COINS, lohi)
        assert tot > 50 and miss0 / tot <= fw.DS_MISSING_MAX_SHARE
        victim = os.path.join(pdir, wins[0].name, "klines", T.COINS[0])                                 # у одной монеты убираем klines всех суток: доля пар > 1 %
        shutil.rmtree(victim)
        miss1, tot1 = fw.ds_missing_pairs(pdir, wins, T.COINS, lohi)
        assert tot1 == tot and miss1 > miss0 and miss1 / tot1 > fw.DS_MISSING_MAX_SHARE
        rc, r = T.run_c(e, "L1", T.D("2026-12-01"), T.D("2026-11-30"), runner=Rec(T.D("2026-11-30")), **cand3_kw(e, cp, cp2, cp3))
        assert rc == 0
        p1 = T.read_json(e.fwd, "point-L1.json")
        assert p1["ds"]["all"]["status"] == "skip" and "без klines предыдущих суток" in p1["ds"]["all"]["reason"] and p1["ds"]["all"]["missing_share"] > fw.DS_MISSING_MAX_SHARE
        assert not any(k.startswith("D_S|all") for k in p1["stats"]) and any(k.startswith("S_base|all") for k in p1["stats"]) and p1["joint"] is None
        assert "НЕ ЧИТАЛАСЬ" in fw.render(p1) and T.read_json(e.fwd, "READ-L1.started")["candidate3_read"] is True


def test_joint_describe_is_a_plain_sum_with_overlap_and_no_verdict():
    d0 = fw.F0
    ms = lambda day, hour: fw.t0_ms(d0 + datetime.timedelta(days=day)) + hour * 3_600_000  # noqa: E731
    a = [("AAA", ms(0, 1), ms(0, 3), 2.0, 0.5, 0.0, "tp"), ("BBB", ms(1, 1), ms(1, 3), 1.0, 0.5, 0.0, "tp"), ("CCC", ms(2, 1), ms(2, 3), -1.0, 0.5, 0.0, "sl")]
    b = [("AAA", ms(0, 2), ms(0, 5), 3.0, 0.5, 0.0, "tp"),                   # пересекается с a[0] (та же монета, интервалы пересекаются)
         ("BBB", ms(1, 10), ms(1, 12), 1.0, 0.5, 0.0, "tp"),                 # та же монета, далеко — без пары
         ("DDD", ms(3, 1), ms(3, 3), -2.0, 0.5, 0.0, "sl")]
    j = fw.joint_describe(a, b, d0 + datetime.timedelta(days=4))
    assert j["n_a"] == 3 and j["n_b"] == 3 and j["n"] == 6 and j["days"] == 5
    assert abs(j["sum_a"] - 0.5) < 1e-12 and abs(j["sum_b"] - 0.5) < 1e-12 and abs(j["sum"] - 1.0) < 1e-12 and abs(j["mean"] - 1.0 / 6) < 1e-12
    assert j["overlap"] == {"a_with_pair": 1, "b_with_pair": 1, "coins_a": 3, "coins_b": 3, "coins_both": 2}
    assert abs(j["maxdd"] - 4.0) < 1e-12 and j["maxdd_pct_margin"] == j["maxdd"] / 250.0 * 100                  # суточные 4,0 / 1,0 / −1,5 / −2,5 / 0: пик 5,0, дно 1,0
    assert "verdict" not in j and "decision" not in j and "alpha" not in j
    assert sum(m["n_a"] + m["n_b"] for m in j["months"].values()) == 6


def test_plan_prints_the_candidate3_alpha():
    with T.tmp() as t:
        e, c, cp, c2, cp2, c3, cp3 = cand3_env(t)
        out = subprocess.run([sys.executable, "-I", "-X", "utf8", os.path.join(HERE, "..", "tk616_forward.py"), "plan", "--freeze", e.freeze, "--root", e.fwd], capture_output=True, text=True,
                             encoding="utf-8", env=dict(os.environ, PYTHONUTF8="1"))
        assert out.returncode == 0 and "tk670-frozen.json (tk670-frozen-1)" in out.stdout and "'D_S': 0.005" in out.stdout and "'S_mk1': 0.005" in out.stdout, out.stdout + out.stderr


def test_replay_ds_compares_outputs_with_tk668_byte_for_byte():
    old_files = fw.FILES_670
    fw.FILES_670 = ("short-trades.csv",)                                                            # подменный движок пишет только trades.csv
    try:
        with T.tmp() as t:
            e, c, cp, c2, cp2, c3, cp3 = cand3_env(t)
            root = os.path.join(t, "r")
            os.makedirs(root)
            T.write_list(os.path.join(root, "symbols-all.txt"), T.COINS)
            T.mk_tree(os.path.join(root, "win"), "", rf.WINDOWS, T.D("2026-11-13"), coins=T.COINS)
            ref = os.path.join(root, "ref")
            os.makedirs(ref)

            def replay(name, **kw):
                a = dict(freeze=e.freeze, freeze_commit="c0ffee", freeze_sha256=fw.sha256_text(e.freeze), bin=e.bin, threads=1, tick_store="", sig_cache="", **cand3_kw(e, cp, cp2, cp3),
                         replay_root=os.path.join(root, name), windows_root=os.path.join(root, "win"), symbols_file=os.path.join(root, "symbols-all.txt"), ref_dir=ref, out="")
                a.update(kw)
                return fw.replay_ds(argparse.Namespace(**a), runner=T.FakeRunner(T.D("2026-11-13")), repo=e.root)

            vid = c3["cells"]["D_S"]["vid"]
            assert replay("rp1") == 3                                                              # эталона нет: не пройдено
            res = T.read_json(root, "rp1", "gate-670c.json")
            assert res["ok"] is False and res["vid"] == vid and all(v["files"]["short-trades.csv"]["ref_sha256"] is None for v in res["windows"].values())
            for w in rf.WINDOWS:                                                                    # эталон = выходам (каталог клетки TK-668 — `S_lx1`)
                os.makedirs(os.path.join(ref, w.name, fw.DS_REF_SUB))
                shutil.copy(os.path.join(root, "rp1", "out", "replay", w.name, vid, "short-trades.csv"), os.path.join(ref, w.name, fw.DS_REF_SUB, "short-trades.csv"))
            assert replay("rp2") == 0 and T.read_json(root, "rp2", "gate-670c.json")["ok"] is True
            open(os.path.join(ref, "wb", fw.DS_REF_SUB, "short-trades.csv"), "a").write("x\n")      # одно различие — не пройдено
            assert replay("rp3") == 3
            r3 = T.read_json(root, "rp3", "gate-670c.json")
            assert r3["windows"]["wb"]["equal"] is False and r3["windows"]["wa"]["equal"] and r3["windows"]["wc"]["equal"]
            T.expect_guard(lambda: replay("rp4", no_candidate3=True), "без --no-candidate3")
            T.expect_guard(lambda: replay("rp5", ref_dir=os.path.join(root, "нет")), "--ref-dir")
            T.expect_guard(lambda: replay("rp6", ds_bin=e.bin), "ds-бинарь")
            late = os.path.join(root, "win", "wc", "klines", T.COINS[0], "%s-klines-2026-11-14.zip" % T.COINS[0])
            open(late, "w").close()
            T.expect_guard(lambda: replay("rp7"), "позже 2026-11-13")
    finally:
        fw.FILES_670 = old_files


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
