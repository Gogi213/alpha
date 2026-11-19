"""Тесты tk626_prep.py (TK-626 §13.28.1 п. 2, 5): вселенная точки, провалы листинга, деревья окон — на синтетике, без сети. `PYTHONUTF8=1 python3 tools/p42/tests/test_tk626_prep.py`."""
import csv
import datetime
import json
import os
import sys
import traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(HERE, ".."))
import test_tk616_forward as T  # noqa: E402
import tk616_forward as fw  # noqa: E402
import tk626_prep as pp  # noqa: E402

D = datetime.date.fromisoformat
BASE = {n: list(c) for n, c in T.UNIS.items()}


def days(a, b):
    return [(D(a) + datetime.timedelta(i)).isoformat() for i in range((D(b) - D(a)).days + 1)]


def listing(spec, ctype=None):
    """spec: {символ: [сутки]}; в листинге у каждого дня aggTrades и klines."""
    files = {}
    for s, ds in spec.items():
        for kind in ("aggTrades", "klines"):
            files[(kind, s)] = {x: (10, "e") for x in ds}
    contracts = {s: {"contract_type": (ctype or {}).get(s, "PERPETUAL"), "status": "TRADING"} for s in spec}
    return contracts, files


SPEC = {
    "AAAUSDT": days("2026-07-01", "2026-11-30"), "BBBUSDT": days("2026-07-05", "2026-10-05"), "CCCUSDT": days("2026-08-01", "2026-11-30"),     # BBB — делистинг до 13.11, остаётся в базе
    "DDDUSDT": days("2026-09-01", "2026-11-30"), "EEEUSDT": days("2026-07-01", "2026-11-30"), "FFFUSDT": days("2026-07-01", "2026-11-30"),
    "NEWAUSDT": days("2026-11-20", "2026-11-30"), "NEWTUSDT": days("2026-11-22", "2026-11-30"),                                              # прирост: крипто и TradFi
    "RETUSDT": ["2026-06-20", "2026-11-18"] + days("2026-11-25", "2026-11-30"),                                                            # торговался до 01.07, без суток 01.07–13.11, вернулся
    "OLDUSDT": days("2026-06-18", "2026-06-25"),                                                                                          # только до 01.07: не вселенная
    "LATEUSDT": days("2026-12-02", "2026-12-05"),                                                                                         # первые сутки после D: ещё не вселенная
}
CT = {"NEWTUSDT": "TRADIFI_PERPETUAL", "EEEUSDT": "TRADIFI_PERPETUAL", "FFFUSDT": "TRADIFI_PERPETUAL"}


def test_universe_is_base_plus_increment_in_stable_order_and_passes_the_tool_guards():
    contracts, files = listing(SPEC, CT)
    lists, rows = pp.build_universe(contracts, files, BASE, "2026-11-30")
    assert lists["all"] == T.COINS + ["RETUSDT", "NEWAUSDT", "NEWTUSDT"]                     # прирост по (первые сутки, символ): RET 18.11, NEWA 20.11, NEWT 22.11
    assert lists["tradfi"] == T.TRADFI + ["NEWTUSDT"] and lists["crypto"] == T.CRYPTO + ["RETUSDT", "NEWAUSDT"]
    assert "OLDUSDT" not in lists["all"] and "LATEUSDT" not in lists["all"]
    by = {r["symbol"]: r for r in rows}
    assert by["AAAUSDT"]["days_0107_1311"] == len([x for x in SPEC["AAAUSDT"] if x <= "2026-11-13"]) and by["BBBUSDT"]["last_day"] == "2026-10-05" and by["BBBUSDT"]["in_base"] == 1
    assert by["NEWAUSDT"]["days_0107_1311"] == 0 and by["NEWAUSDT"]["first_day"] == "2026-11-20" and by["NEWAUSDT"]["last_day"] == "2026-11-30" and by["NEWAUSDT"]["tradfi"] == 0
    assert by["RETUSDT"]["first_day"] == "2026-11-18" and by["NEWTUSDT"]["tradfi"] == 1 and by["NEWTUSDT"]["contract_type"] == "TRADIFI_PERPETUAL"
    with T.tmp() as t:
        e = T.Env(t)
        f = fw.load_freeze(e.freeze)
        out = os.path.join(t, "L1", "universe")
        pp.write_universe(out, lists, rows)
        r = fw.check_universe(f, out, e.base_dir, {"id": "L1", "d": "2026-11-30"})                  # то, что пишет подготовка, проходит охрану инструмента
        assert r["lists"] == lists and b"\r" not in open(os.path.join(out, "contracts.csv"), "rb").read()
        # точка L2: тот же прирост + новый; список точки k — префикс списка точки k + 1
        contracts2, files2 = listing(dict(SPEC, LATEUSDT=days("2026-12-02", "2026-12-31"), NEXTUSDT=days("2026-12-10", "2026-12-31")), CT)
        lists2, rows2 = pp.build_universe(contracts2, files2, BASE, "2026-12-31", pp.read_prev_classes(out))
        assert lists2["all"][:len(lists["all"])] == lists["all"] and lists2["all"][len(lists["all"]):] == ["LATEUSDT", "NEXTUSDT"]
        for n in fw.UNI_NAMES:
            assert lists2[n][:len(lists[n])] == lists[n]


def test_class_is_recorded_at_first_appearance_and_a_later_change_stops():
    contracts, files = listing(SPEC, CT)
    lists, rows = pp.build_universe(contracts, files, BASE, "2026-11-30")
    with T.tmp() as t:
        pp.write_universe(t, lists, rows)
        prev = pp.read_prev_classes(t)
        assert prev["NEWTUSDT"] == 1 and prev["NEWAUSDT"] == 0 and prev["AAAUSDT"] == 0
        flipped = dict(contracts, NEWTUSDT={"contract_type": "PERPETUAL", "status": "TRADING"})
        T.expect_guard(lambda: pp.build_universe(flipped, files, BASE, "2026-12-31", prev), "класс в предыдущей точке")
        gone = {k: v for k, v in contracts.items() if k != "NEWAUSDT"}                                       # нет в exchangeInfo — крипто (как AERGOUSDT)
        l2, _ = pp.build_universe(gone, files, BASE, "2026-11-30")
        assert "NEWAUSDT" in l2["crypto"]
    base_leak = {**files, ("aggTrades", "ZZZUSDT"): {"2026-10-01": (1, "e"), "2026-11-20": (1, "e")}}
    T.expect_guard(lambda: pp.build_universe(contracts, base_leak, BASE, "2026-11-30"), "должен был быть в базе")


def test_missing_gaps_and_tail_cover_only_the_forward_days():
    contracts, files = listing(SPEC, CT)
    miss = pp.missing_rows(files, ["AAAUSDT", "NEWAUSDT", "BBBUSDT"], "2026-11-30")
    assert {(k, s) for k, s, *_ in miss} == {("aggTrades", "AAAUSDT"), ("klines", "AAAUSDT"), ("aggTrades", "NEWAUSDT"), ("klines", "NEWAUSDT")}      # BBB делистингован — файлов ≥ 14.11 нет
    assert min(r[2] for r in miss) == "2026-11-14" and max(r[2] for r in miss) == "2026-11-30" and len(miss) == 2 * 17 + 2 * 11
    holes = dict(files)
    holes[("klines", "AAAUSDT")] = {x: v for x, v in files[("klines", "AAAUSDT")].items() if x not in ("2026-11-16", "2026-11-17")}
    holes[("aggTrades", "CCCUSDT")] = {x: v for x, v in files[("aggTrades", "CCCUSDT")].items() if x != "2026-11-29"}
    holes[("aggTrades", "DDDUSDT")] = {x: v for x, v in files[("aggTrades", "DDDUSDT")].items() if x <= "2026-11-28"}                              # хвост: 29–30.11 нет
    gaps, tail = pp.gap_rows(holes, ["AAAUSDT", "CCCUSDT", "DDDUSDT", "BBBUSDT"], "2026-11-30")
    assert gaps == [("klines", "AAAUSDT", "2026-11-16"), ("klines", "AAAUSDT", "2026-11-17"), ("aggTrades", "CCCUSDT", "2026-11-29")]
    assert tail == [("aggTrades", "DDDUSDT", "2026-11-28")]


def test_tree_links_first_existing_source_and_reports_missing_days():
    with T.tmp() as t:
        old, dl, rest = (os.path.join(t, n) for n in ("old", "dl", "rest"))

        def touch(path, body=b"x"):
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "wb").write(body)

        touch(old + "/klines/AAAUSDT/2026-10-16.csv", b"old")
        touch(dl + "/klines-csv/AAAUSDT/2026-10-16.csv", b"dl")                                           # старое дерево раньше скачанного
        touch(dl + "/klines-csv/AAAUSDT/2026-11-14.csv", b"dl2")
        touch(rest + "/klines-csv/AAAUSDT/2026-11-15.csv", b"rest")
        touch(old + "/aggtrades/AAAUSDT/AAAUSDT-aggTrades-2026-10-16.zip", b"zip")
        touch(dl + "/aggtrades/BBBUSDT/BBBUSDT-aggTrades-2026-11-14.zip", b"zipb")
        touch(old + "/klines/AAAUSDT/funding.csv", b"f")
        touch(dl + "/klines-csv/AAAUSDT/2026-11-16.csv", b"")                                              # пустой файл — не файл
        kl = [old + "/klines/{s}/{d}.csv", dl + "/klines-csv/{s}/{d}.csv", rest + "/klines-csv/{s}/{d}.csv"]
        tr = [old + "/aggtrades/{s}/{s}-aggTrades-{d}.zip", dl + "/aggtrades/{s}/{s}-aggTrades-{d}.zip"]
        w = os.path.join(t, "w1")
        ds = pp.day_list("2026-10-16", D("2026-11-16"))
        assert ds[0] == "2026-10-16" and ds[-1] == "2026-11-16" and len(ds) == 32 and pp.day_list("2026-10-16", D("2027-03-01"))[-1] == "2026-12-28"           # данные окна — до T0 + 73
        cnt, miss = pp.build_tree(w, ["AAAUSDT", "BBBUSDT"], ds, kl, tr, [old + "/klines/{s}/funding.csv"])
        assert cnt == {"klines": 3, "aggtrades": 2, "funding": 1}
        assert open(w + "/klines/AAAUSDT/2026-10-16.csv", "rb").read() == b"old" and open(w + "/klines/AAAUSDT/2026-11-15.csv", "rb").read() == b"rest"
        assert os.path.isdir(w + "/aggtrades/AAAUSDT") and os.path.isdir(w + "/klines/BBBUSDT") and os.path.exists(w + "/klines/AAAUSDT/funding.csv")
        assert ("klines", "AAAUSDT", "2026-11-16") in miss and ("aggtrades", "AAAUSDT", "2026-11-14") in miss and len(miss) == 2 * 2 * 32 - 5


def test_tree_command_refuses_dates_off_the_freeze_and_existing_directories():
    with T.tmp() as t:
        e = T.Env(t)
        T.write_universe(t, "U", [])
        base = ["tree", "--out", os.path.join(t, "P"), "--universe", os.path.join(t, "U", "universe"), "--freeze", e.freeze, "--dl", t + "/dl", "--rest", t + "/rest"]
        assert pp.main(base + ["--d", "2026-11-16"]) == 3                                                  # не дата точки и нет --allow-test
        assert pp.main(base + ["--d", "2026-11-16", "--windows", "w1"]) == 3
        assert pp.main(base + ["--d", "2026-11-16", "--windows", "w1", "--allow-test"]) == 0
        assert os.path.isdir(os.path.join(t, "P", "w1", "klines", T.COINS[0])) and os.path.exists(os.path.join(t, "P", "nofile-w1.tsv"))
        assert pp.main(base + ["--d", "2026-11-16", "--windows", "w1", "--allow-test"]) == 3                 # поверх не строится
        fresh = ["tree", "--out", os.path.join(t, "P2"), "--universe", os.path.join(t, "U", "universe"), "--freeze", e.freeze, "--dl", t + "/dl", "--rest", t + "/rest"]
        assert pp.main(fresh + ["--d", "2026-11-30"]) == 0 and os.path.isdir(os.path.join(t, "P2", "w1")) and not os.path.exists(os.path.join(t, "P2", "w2"))      # дата точки L1 → её окна (w1)


def test_check_gate_needs_a_directory_per_contract_and_no_late_days():
    with T.tmp() as t:
        e = T.Env(t)
        T.write_universe(t, "U", [])
        args = ["--out", os.path.join(t, "P"), "--universe", os.path.join(t, "U", "universe"), "--freeze", e.freeze, "--d", "2026-11-16", "--windows", "w1"]
        assert pp.main(["check"] + args) == 3                                                               # дерева нет
        assert pp.main(["tree"] + args + ["--allow-test", "--dl", t + "/dl", "--rest", t + "/rest"]) == 0
        assert pp.main(["check"] + args) == 0
        os.rmdir(os.path.join(t, "P", "w1", "klines", T.COINS[0]))
        assert pp.main(["check"] + args) == 3                                                               # у контракта нет каталога
        os.makedirs(os.path.join(t, "P", "w1", "klines", T.COINS[0]))
        open(os.path.join(t, "P", "w1", "klines", T.COINS[0], "2026-11-17.csv"), "w").close()               # сутки позже D
        assert pp.main(["check"] + args) == 3


def test_universe_command_writes_lists_contracts_missing_and_gaps():
    contracts, files = listing(SPEC, CT)
    with T.tmp() as t:
        e = T.Env(t)
        ld = os.path.join(t, "list")
        os.makedirs(ld)
        json.dump({"contracts": contracts}, open(os.path.join(ld, "contracts.json"), "w"))
        with open(os.path.join(ld, "files.tsv"), "w") as f:
            for (k, s), ds in sorted(files.items()):
                for day, (size, etag) in sorted(ds.items()):
                    f.write("\t".join([k, s, day, str(size), etag]) + "\n")
        out = os.path.join(t, "U")
        assert pp.main(["universe", "--list-dir", ld, "--base-dir", e.base_dir, "--d", "2026-11-30", "--out", out]) == 0
        assert open(os.path.join(out, "symbols-all.txt"), encoding="utf-8").read().strip().split(",")[:6] == T.COINS
        rows = list(csv.DictReader(open(os.path.join(out, "contracts.csv"), encoding="utf-8")))
        assert [r["symbol"] for r in rows if r["in_base"] == "0"] == ["RETUSDT", "NEWAUSDT", "NEWTUSDT"]
        miss = [l.split("\t") for l in open(os.path.join(ld, "missing.tsv"), encoding="utf-8").read().splitlines()]
        assert miss and all("2026-11-14" <= r[2] <= "2026-11-30" for r in miss) 
        gaps = [l.split("	") for l in open(os.path.join(ld, "gaps.tsv"), encoding="utf-8").read().splitlines()]
        assert len(gaps) == 12 and {g[1] for g in gaps} == {"RETUSDT"} and {g[2] for g in gaps} == set(days("2026-11-19", "2026-11-24"))                       # RET: провал 19–24.11 в обоих видах


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
