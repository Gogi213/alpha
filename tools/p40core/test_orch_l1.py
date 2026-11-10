"""TK-518: ноги Bybit/Gate из L1 бокса — строгая выборка (нет суток = сбой), распаковка, merge. Запуск на сервере счёта: python3 test_orch_l1.py
rsync с бокса подменён копированием из локального «ящика» <tmp>/box/alpha/stakan; zstd — настоящий."""
import importlib.util, os, pathlib, subprocess, tempfile, types

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("orch", os.path.join(HERE, "orch.py"))
orch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orch)


def make_box(tmp, ex="bybit", sym="XUSDT", day="2026-10-05", hours=(0, 1, 5), ok=True, skew=0):
    d = tmp / "box" / "alpha" / "stakan" / orch.l1_rel(ex, sym, day)
    d.mkdir(parents=True)
    size = 0
    for h in hours:
        raw = tmp / f"{h:02d}.l1.bin"
        raw.write_bytes(bytes([h]) * 66)   # две 33-байтные строки
        subprocess.run(["zstd", "-q", "-f", "-o", str(d / f"{h:02d}.l1.bin.zst"), str(raw)], check=True)
        size += os.path.getsize(d / f"{h:02d}.l1.bin.zst")
    if ok:
        (d / ".ok").write_text("raw=1 l1=%d tr=0 files=%d src=box\n" % (size + skew, len(hours)))
    return d


def fake_rsync(tmp):
    def call(cmd, **kw):
        src = str(tmp / "box") + "/" + cmd[-2].split(":", 1)[1]
        return subprocess.call(["cp", "-a", src + ".", cmd[-1]]) if os.path.isdir(src) else 23
    orch.subprocess = types.SimpleNamespace(**dict({k: getattr(subprocess, k) for k in dir(subprocess) if not k.startswith("__")}, call=call))


def test_fetch_and_unpack_ok(tmp):
    make_box(tmp)
    raw, bins, log = str(tmp / "raw"), str(tmp / "bins"), str(tmp / "log")
    tf, nb, w, c = orch.unpack_l1("bybit", "XUSDT", "2026-10-05", raw, bins, log)
    assert sorted(os.listdir(f"{bins}/B")) == ["00.l1.bin", "01.l1.bin", "05.l1.bin"]
    assert open(f"{bins}/B/05.l1.bin", "rb").read() == bytes([5]) * 66
    assert os.path.isdir(f"{bins}/T") and not os.listdir(f"{bins}/T")
    assert nb > 0
    orch.merge([bins], str(tmp / "mrg"), "B")      # merge линкует .l1.bin как <idx>.bin
    assert sorted(os.listdir(tmp / "mrg" / "B")) == ["00.bin", "01.bin", "05.bin"]
    assert open(tmp / "mrg" / "B" / "05.bin", "rb").read() == bytes([5]) * 66
    orch.drop_heavy(raw)                           # drop_heavy убирает .zst
    assert not [f for _, _, fs in os.walk(raw) for f in fs if f.endswith(".zst")]


def refused(tmp, why):
    try:
        orch.fetch_l1("bybit", "XUSDT", "2026-10-05", str(tmp / "raw"), str(tmp / "log"))
    except RuntimeError as e:
        assert "L1 p40l1/bybit_futures/XUSDT/2026-10-05" in str(e), (why, str(e))
    else:
        raise AssertionError(f"{why}: ожидался отказ")


def test_strict_refusal_no_day(tmp):
    refused(tmp, "нет суток на боксе")


def test_strict_refusal_no_ok(tmp):
    make_box(tmp, ok=False)
    refused(tmp, "нет .ok")


def test_strict_refusal_size(tmp):
    make_box(tmp, skew=1)
    refused(tmp, "размер не по метке")


def test_strict_refusal_files(tmp):
    (make_box(tmp) / "05.l1.bin.zst").unlink()
    refused(tmp, "число файлов не по метке")


def test_strict_refusal_files0(tmp):
    (make_box(tmp, hours=()) / ".ok").write_text("raw=1 l1=0 tr=0 files=0 src=box\n")
    refused(tmp, "files=0")


def test_chft_leg_with_trades(tmp):
    """TK-522: нога CHFT — l1 + сделки (.tr.zst -> T/<ЧЧ>.tr -> merge линкует как <idx>.bin); метка «files=a/b 404=k»; aux-файлы сырья — только mark/OI/ликвидации"""
    d = make_box(tmp, ex="okx", sym="X-USDT-SWAP", hours=(0, 7))
    tsize = 0
    for h in (0, 7):
        raw = tmp / f"{h:02d}.tr"
        raw.write_bytes(bytes([h + 1]) * 50)
        subprocess.run(["zstd", "-q", "-f", "-o", str(d / f"{h:02d}.tr.zst"), str(raw)], check=True)
        tsize += os.path.getsize(d / f"{h:02d}.tr.zst")
    l1 = sum(os.path.getsize(d / f"{h:02d}.l1.bin.zst") for h in (0, 7))
    (d / ".ok").write_text("raw=1 l1=%d tr=%d files=4/48 404=22\n" % (l1, tsize))
    raw, bins, log = str(tmp / "raw"), str(tmp / "bins"), str(tmp / "log")
    orch.unpack_l1("okx", "X-USDT-SWAP", "2026-10-05", raw, bins, log)
    assert sorted(os.listdir(f"{bins}/B")) == ["00.l1.bin", "07.l1.bin"] and sorted(os.listdir(f"{bins}/T")) == ["00.tr", "07.tr"]
    orch.merge([bins], str(tmp / "mrg"), "T")
    assert open(tmp / "mrg" / "T" / "07.bin", "rb").read() == bytes([8]) * 50
    orch.drop_heavy(raw)
    assert not [f for _, _, fs in os.walk(raw) for f in fs if f.endswith(".zst")]
    fs = orch.box_files("okx", "X-USDT-SWAP", "2026-10-05", aux_only=True)
    assert len(fs) == 72 and not [f for f in fs if "orderbook" in f or "trades" in f] and orch.box_files("bybit", "X", "2026-10-05", aux_only=True) == []


def test_rawcopy_label_counts_l1_only(tmp):   # src=rawcopy (raw-conv): files= — число l1-файлов, .tr.zst рядом; байты сверяются, число l1 — по метке; не-rawcopy метка files=2 при 4 файлах — по-прежнему отказ
    d = make_box(tmp, ex="okx", sym="X-USDT-SWAP", hours=(0, 7))
    tsize = 0
    for h in (0, 7):
        raw = tmp / f"{h:02d}.tr"
        raw.write_bytes(bytes([h + 1]) * 50)
        subprocess.run(["zstd", "-q", "-f", "-o", str(d / f"{h:02d}.tr.zst"), str(raw)], check=True)
        tsize += os.path.getsize(d / f"{h:02d}.tr.zst")
    l1 = sum(os.path.getsize(d / f"{h:02d}.l1.bin.zst") for h in (0, 7))
    args = ("okx", "X-USDT-SWAP", "2026-10-05", str(tmp / "raw"), str(tmp / "log"))
    (d / ".ok").write_text("raw=1 l1=%d tr=%d files=2 src=rawcopy\n" % (l1, tsize))
    assert len(orch.fetch_l1(*args)) == 4
    for body in ("raw=1 l1=%d tr=%d files=3 src=rawcopy\n", "raw=1 l1=%d tr=%d files=2 src=box\n"):
        (d / ".ok").write_text(body % (l1, tsize))
        try:
            orch.fetch_l1("okx", "X-USDT-SWAP", "2026-10-05", str(tmp / "raw2"), str(tmp / "log"))
            raise AssertionError(f"{body!r}: ожидался отказ")
        except RuntimeError:
            pass


def test_chft_l1_missing_for_fallback(tmp):
    """TK-522 --l1-fallback: нет суток / нет метки / у CHFT нет сделок (Binance TK-521: tr=0) -> L1Missing (нога идёт из сырья); несовпадение размера по метке — по-прежнему RuntimeError без отката"""
    args = ("okx", "X-USDT-SWAP", "2026-10-05", str(tmp / "raw"), str(tmp / "log"))
    for why in ("нет суток", "tr=0"):
        if why == "tr=0":
            make_box(tmp, ex="okx", sym="X-USDT-SWAP", hours=(0,))
        try:
            orch.fetch_l1(*args)
        except orch.L1Missing:
            pass
        else:
            raise AssertionError(f"{why}: ожидался L1Missing")
    d = make_box(tmp, ex="okx", sym="X-USDT-SWAP", day="2026-10-06", hours=(0, 1), skew=1)
    try:
        orch.fetch_l1("okx", "X-USDT-SWAP", "2026-10-06", str(tmp / "raw"), str(tmp / "log"))
    except orch.L1Missing:
        raise AssertionError("размер не по метке не должен быть откатом на сырьё")
    except RuntimeError:
        pass


def test_binance_book_l1_trades_raw(tmp):   # --l1-trades-raw: L1 CHFT без сделок (tr=0) -> книга из L1, не L1Missing; сделки — из parquet бокса (box_files trades=True)
    make_box(tmp, ex="binance", sym="X", hours=(0, 1))
    fs = orch.fetch_l1("binance", "X", "2026-10-05", str(tmp / "raw"), str(tmp / "log"), trades_raw=True)
    assert len(fs) == 2 and not any(f.endswith(".tr.zst") for f in fs)
    aux = orch.box_files("binance", "X", "2026-10-05", aux_only=True)
    trd = orch.box_files("binance", "X", "2026-10-05", aux_only=True, trades=True)
    assert not any("trades" in f for f in aux) and sum("X_trades.parquet" in f for f in trd) == 24 and len(trd) == len(aux) + 24


def test_l1_leg_flags():
    a = types.SimpleNamespace(raw_legs=False, raw_ex=set())
    assert all(orch.l1_leg(a, ex) for ex in ("bybit", "gate", "okx", "binance", "bitget", "aster", "hyperliquid"))   # TK-522: все биржи
    assert not orch.l1_leg(types.SimpleNamespace(raw_legs=True, raw_ex=set()), "bybit")


def test_raw_ex_per_exchange():   # --raw-ex: L1 биржи ещё неверен (Bitget до CARRY, E-17) -> нога из сырья, остальные — L1
    a = types.SimpleNamespace(raw_legs=False, raw_ex={"bitget", "hyperliquid"})
    assert not orch.l1_leg(a, "bitget") and not orch.l1_leg(a, "hyperliquid")
    assert orch.l1_leg(a, "okx") and orch.l1_leg(a, "binance") and orch.l1_leg(a, "bybit")


def test_l1_src_per_exchange():   # P40_L1_SRC_<БИРЖА>: HL на srv (/data/p40l1-hl), остальные — общий источник
    os.environ["P40_L1_SRC_HYPERLIQUID"] = "/data/p40l1-hl"
    try:
        assert orch.l1_src("hyperliquid") == "/data/p40l1-hl" and orch.l1_src("okx") == orch.L1_SRC
    finally:
        del os.environ["P40_L1_SRC_HYPERLIQUID"]
    assert orch.l1_src("hyperliquid") == orch.L1_SRC


def test_old_label_without_files(tmp):   # метки Bybit/Gate старого вида (raw= l1= tr=) без files=: число файлов — с диска, размер сверяется
    d = make_box(tmp, hours=(0, 1, 5))
    kv = dict(x.split("=", 1) for x in (d / ".ok").read_text().split())
    (d / ".ok").write_text("raw=1 l1=%s tr=0 src=box\n" % kv["l1"])
    fs = orch.fetch_l1("bybit", "XUSDT", "2026-10-05", str(tmp / "raw"), str(tmp / "log"))
    assert len(fs) == 3
    (d / ".ok").write_text("raw=1 l1=%d tr=0 src=box\n" % (int(kv["l1"]) + 1))
    try:
        orch.fetch_l1("bybit", "XUSDT", "2026-10-05", str(tmp / "raw2"), str(tmp / "log"))
        raise AssertionError("размер не по метке без files= должен быть сбоем")
    except RuntimeError:
        pass


def test_validity_coverage_gaps(tmp):   # П-40 §16.5: час без строки validity = дыра покрытия; 'day' (любой статус) покрывает все 24 часа; seen не меняет bad
    import gzip
    vp = str(tmp / "v.csv.gz")
    rows = ["ex,contract,date,hour,status"] + [f"binance_futures,AUSDT,2026-09-17,{h},ok" for h in range(24)]
    rows += [f"binance_futures,AUSDT,2026-09-18,{h},{'gap' if h == 3 else 'ok'}" for h in range(23)]   # 23 часа: нет часа 23
    rows += ["okx_futures,AUSDT,2026-09-17,day,missing"]
    with gzip.open(vp, "wt") as f:
        f.write(chr(10).join(rows) + chr(10))
    seen = {}
    bad = orch.load_validity([vp], seen)
    assert bad == {("binance_futures", "AUSDT", "2026-09-18"): {3}, ("okx_futures", "AUSDT", "2026-09-17"): set(range(24))}
    assert orch.load_validity([vp]) == bad
    pairs = [{"exA": "binance", "symA": "AUSDT", "exB": "okx", "symB": "AUSDT"}]
    assert orch.validity_gaps(seen, pairs, ["2026-09-17"]) == {}
    assert orch.validity_gaps(seen, pairs, ["2026-09-17", "2026-09-18"]) == {("binance_futures", "AUSDT", "2026-09-18"): 1, ("okx_futures", "AUSDT", "2026-09-18"): 24}
    assert orch.validity_gaps(seen, [{"exA": "gate", "symA": "BUSDT", "exB": "okx", "symB": "AUSDT"}], ["2026-09-17"]) == {("gate", "BUSDT", "2026-09-17"): 24}, "нога без строк — 24 часа дыры"


def test_l1_validity_rows():   # Судья 10.11 §2: строка validity на КАЖДЫЙ час плана; нет состояний -> no_data, кросс > 1 % -> crossed>1%, иначе ok; prev_gaps = NA
    stats = {0: (1000, 0, 500, 900), 1: (1000, 10, 400, 800), 2: (1000, 11, 400, 800), 5: (0, 0, 0, 0), 6: (1, 0, 0, 0), 24 + 3: (10, 0, 7, 7)}   # часы чанка из двух суток; 1: ровно 1 % — годен; 2: 1,1 % — негоден
    rows = orch.l1_validity_rows("okx", "AUSDT", ["2026-09-17", "2026-09-18"], stats)
    assert len(rows) == 48 and {r[0] for r in rows} == {"okx_futures"}
    by = {(r[2], r[3]): r for r in rows}
    assert by[("2026-09-17", "00")][4] == "ok" and by[("2026-09-17", "00")][6] == "NA"
    assert by[("2026-09-17", "01")][4] == "ok" and by[("2026-09-17", "02")][4] == "crossed>1%"
    assert by[("2026-09-17", "05")][4] == "no_data" and by[("2026-09-17", "23")][4] == "no_data", "часа без файла в stats нет -> no_data, не пропуск"
    assert by[("2026-09-17", "06")][4] == "no_diffs", "одно состояние в часе = только снимок"
    assert by[("2026-09-18", "03")][4] == "ok" and by[("2026-09-18", "03")][8] == 7
    assert sum(r[4] == "ok" for r in rows) == 3


def test_box_rsync_retry_and_cap(tmp):   # 10.11: rc 12 (бокс закрыл соединение при 48 rsync) -> повтор, а не сбой актива; потолок --box-conn; 23/11 повторяться не должны
    import threading
    calls, live, peak = [], [0], [0]
    seq = {"a": [12, 255, 0], "b": [23], "c": [12, 12, 12, 12], "d": [11]}
    lock = threading.Lock()

    def call(cmd, **kw):
        k = cmd[-2]
        with lock:
            calls.append(k)
            live[0] += 1
            peak[0] = max(peak[0], live[0])
        import time
        time.sleep(0.02)
        with lock:
            live[0] -= 1
        return seq[k].pop(0) if k in seq else 0
    sl = orch.time.sleep
    orch.time.sleep = lambda s: None
    orch.subprocess = types.SimpleNamespace(**dict({k: getattr(subprocess, k) for k in dir(subprocess) if not k.startswith("__")}, call=call))
    log = str(tmp / "log")
    try:
        assert orch.box_rsync(["x", "a", "dst"], log) == 0 and calls.count("a") == 3, "12, 255 -> повтор до успеха"
        assert orch.box_rsync(["x", "b", "dst"], log) == 23 and calls.count("b") == 1, "23 без повтора"
        assert orch.box_rsync(["x", "d", "dst"], log) == 11 and calls.count("d") == 1, "11 без повтора"
        assert orch.box_rsync(["x", "c", "dst"], log) == 12 and calls.count("c") == orch.BOX_TRIES, "после BOX_TRIES попыток rc отдаётся вызывающему"
        assert "повтор 1/" in open(log).read()
        calls.clear()
        orch.BOX_SEM = threading.BoundedSemaphore(3)
        ts = [threading.Thread(target=orch.box_rsync, args=(["x", f"e{i}", "dst"], log)) for i in range(12)]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert len(calls) == 12 and peak[0] <= 3, (len(calls), peak[0])
    finally:
        orch.BOX_SEM = None
        orch.time.sleep = sl


def test_supplier_empty_day_is_gap_not_error(tmp):
    """Судья 10.11 класс 3: метка files=0/48 404=48 -> [] (дыра на 24 часа), не RuntimeError; частичные сутки -> недостающие часы; l1_gaps.csv без повторов"""
    d = tmp / "box" / "alpha" / "stakan" / orch.l1_rel("okx", "XUSDT", "2026-10-05")
    d.mkdir(parents=True)
    (d / ".ok").write_text("raw=0 l1=0 tr=0 files=0/48 404=48 src=box" + chr(10))
    assert orch.fetch_l1("okx", "XUSDT", "2026-10-05", str(tmp / "raw"), str(tmp / "log")) == []
    tf, nb, w, c = orch.unpack_l1("okx", "XUSDT", "2026-10-05", str(tmp / "raw"), str(tmp / "bins"), str(tmp / "log"), fs=[])
    assert os.path.isdir(f"{tmp}/bins/B") and not os.listdir(f"{tmp}/bins/B")
    assert orch.gap_hours([]) == list(range(24))
    assert orch.gap_hours([f"/x/{h:02d}.l1.bin.zst" for h in (0, 1, 5)] + ["/x/05.tr.zst"]) == [h for h in range(24) if h not in (0, 1, 5)]
    orch.SEEN_GAPS.clear()
    orch.record_gap(str(tmp), "okx", "XUSDT", "2026-10-05", orch.gap_hours([]))
    orch.record_gap(str(tmp), "okx", "XUSDT", "2026-10-05", orch.gap_hours([]))
    orch.record_gap(str(tmp), "bybit", "YUSDT", "2026-10-06", [3, 4])
    rows = open(tmp / "l1_gaps.csv").read().splitlines()
    assert len(rows) == 3 and rows[1].startswith("okx,XUSDT,2026-10-05,24,404,") and rows[2].startswith("bybit,YUSDT,2026-10-06,2,gap_hours,3 4"), rows


def test_bare_404_label_is_gap_not_error(tmp):
    """TK-522 (класс 3): Bybit/Gate метка "raw=0 l1=0 tr=0 404" (голый 404, без files=) -> [] (дыра на 24 часа); пустые сутки без 404 и без файлов — прежняя ошибка"""
    d = tmp / "box" / "alpha" / "stakan" / orch.l1_rel("bybit", "DNAUSDT", "2026-09-17")
    d.mkdir(parents=True)
    (d / ".ok").write_text("raw=0 l1=0 tr=0 404" + chr(10))
    assert orch.fetch_l1("bybit", "DNAUSDT", "2026-09-17", str(tmp / "raw"), str(tmp / "log")) == []
    (d / ".ok").write_text("raw=0 l1=0 tr=0" + chr(10))
    try:
        orch.fetch_l1("bybit", "DNAUSDT", "2026-09-17", str(tmp / "raw"), str(tmp / "log"))
        raise AssertionError("ждали RuntimeError")
    except RuntimeError:
        pass


def test_gap_hours_become_merged_holes(tmp):
    """дыры L1 -> P40_HOLES: пустые сутки одной ноги, часть часов другой; склейка в интервалы"""
    bad = {("okx_futures", "XUSDT", "2026-10-06"): set(range(24)), ("bybit", "YUSDT", "2026-10-05"): {23}, ("bybit", "YUSDT", "2026-10-06"): {0, 7}}
    days = ["2026-10-05", "2026-10-06", "2026-10-07"]
    n = orch.write_holes(str(tmp / "h"), bad, [("okx", "XUSDT"), ("bybit", "YUSDT")], days)
    t0 = orch.calendar.timegm(orch.time.strptime("2026-10-05", "%Y-%m-%d")) * 1000
    H = 3600000
    got = [tuple(map(int, l.split(","))) for l in open(tmp / "h").read().splitlines()]
    assert n == 1 and got == [(t0 + 23 * H, t0 + 48 * H)], (n, got)   # 23:00 д1 + сутки д2 (0 ч и 7 ч другой ноги внутри) склеены в один интервал; д3 годен


def test_smax_l1_is_descriptive_not_a_status():   # Судья 10.11 §2 п.1: S_max = max(1 с, медиана почасовых p99) по контракту (G0 :73); часы no_data/no_diffs возраста не дают; статус часа не меняется
    import smax_l1
    mk = lambda ex, c, h, st, n, p, m: {"ex": ex, "contract": c, "date": "2026-10-05", "hour": f"{h:02d}", "status": st, "checked": str(n), "age_p99_ms": str(p), "age_max_ms": str(m)}
    rows = [mk("okx_futures", "XUSDT", 0, "ok", 100, 2000, 5000), mk("okx_futures", "XUSDT", 1, "ok", 100, 4000, 400000), mk("okx_futures", "XUSDT", 2, "ok", 100, 9000, 6000),
            mk("okx_futures", "XUSDT", 3, "no_data", 0, 0, 0), mk("okx_futures", "XUSDT", 4, "no_diffs", 1, 0, 0), mk("bybit", "YUSDT", 0, "ok", 50, 300, 800)]
    got = {(e, c): (s, n, b) for e, c, s, n, b in smax_l1.smax_rows(rows)}
    assert got[("okx_futures", "XUSDT")] == (4.0, 3, 1), got   # медиана 2/4/9 с = 4 с; 3 часа с возрастом; один час age_max > 300 с — только счётчик
    assert got[("bybit", "YUSDT")] == (1.0, 1, 0), got   # нижняя граница 1 с
    assert smax_l1.quantiles([1.0, 2.0, 3.0, 4.0, 5.0]) == [1.0, 1.0, 2.0, 3.0, 4.0, 5.0, 5.0]


def test_gap_e2e_check():   # Судья 10.11 §7 п.7: пара с дырой посередине — эпизоды до и после, у дыры trunc=2; позиция trunc=0 через дыру и старт в дыре — нарушение
    import gap_e2e_check as g
    holes = [(1000, 2000), (5000, 6000)]
    row = lambda t0, ex, tr, kind="x": {"t0_ms": str(t0), "exit_ms": str(ex), "exit_kind": kind, "trunc": tr}
    ok = [row(100, 200, "0"), row(900, -1, "2", "trunc_no_entry"), row(950, 1000, "2", "trunc_taker"), row(2500, 2600, "0"), row(6500, 6700, "0")]
    bad, s = g.check(ok, holes, need_both=True)
    assert not bad and (s["bracketed"], s["trunc2"]) == (2, 2), (bad, s)
    assert len(g.check([row(900, 1100, "0")], holes)[0]) == 1, "позиция trunc=0 через дыру"
    assert len(g.check([row(1500, 1600, "0")], holes)[0]) == 1 and len(g.check([row(1500, 1600, "2")], holes)[0]) == 1, "старт в дыре"
    assert len(g.check([row(7000, 7001, "2")], holes)[0]) == 1, "trunc=2 без дыры после t0"
    assert len(g.check([row(100, 200, "0")], holes, need_both=True)[0]) == 1, "нет эпизодов после дыр: ни одна дыра не обрамлена"
    assert g.check([row(100, 200, "")], [])[0] == []


def test_write_age_files_and_k1_shift(tmp):   # Судья 10.11 §2 п. 2: <база>.age.csv на объединении t0 вариантов без сдвига; K1 — отдельный ряд B со сдвигом; ядро подменено скриптом `age <dir> <t0> [B]`
    core = tmp / "core.sh"
    core.write_text("\n".join(['#!/bin/sh', 's=0; [ "$4" = B ] && s=${P40_SHIFT_B_MS:-0}', 'while read t; do echo "$t $((t % 1000 + s))"; done < "$3"', ""]))
    core.chmod(0o755)
    base = str(tmp / "p")
    for name, ts in (("", (1500, 2750)), (".k2", (2750, 3100)), (".k1p60", (4200,))):
        pathlib.Path(f"{base}{name}.features.csv").write_text("\n".join(["theta,t_start"] + [f"5.04,{t}" for t in ts]) + "\n")
    orch.write_age(str(core), base, ["k2", "k1p60"], "A", "B", dict(os.environ), {"k2": {"P40_K2": "1"}, "k1p60": {"P40_SHIFT_B_MS": "60000"}})
    rd = lambda n: pathlib.Path(f"{base}{n}.age.csv").read_text().split()
    assert rd("") == ["t0_ms,ageA_ms,ageB_ms", "1500,500,500", "2750,750,750", "3100,100,100"], rd("")
    assert rd(".k2") == rd(""), "варианты без сдвига делят ряд базы"
    assert rd(".k1p60") == ["t0_ms,ageA_ms,ageB_ms", "4200,200,60200"], rd(".k1p60")


def test_l1_cache_replaces_box(tmp):   # TK-522: P40_L1_CACHE (prefetch_l1.py) -> сутки с диска srv без бокса; метка и размеры сверяются как всегда; без .ok в кэше — путь через бокс
    import shutil
    d = make_box(tmp)
    cache = tmp / "cache" / orch.l1_rel("bybit", "XUSDT", "2026-10-05")
    shutil.copytree(d, cache)
    shutil.rmtree(tmp / "box")
    orch.subprocess = subprocess
    os.environ["P40_L1_CACHE"] = str(tmp / "cache")
    try:
        fs = orch.fetch_l1("bybit", "XUSDT", "2026-10-05", str(tmp / "raw"), str(tmp / "log"))
        assert [os.path.basename(f) for f in fs] == ["00.l1.bin.zst", "01.l1.bin.zst", "05.l1.bin.zst"]
        (cache / ".ok").unlink()
        fake_rsync(tmp)
        try:
            orch.fetch_l1("bybit", "XUSDT", "2026-10-05", str(tmp / "raw2"), str(tmp / "log"))
        except Exception as e:
            assert "XUSDT" in str(e)
        else:
            raise AssertionError("без .ok в кэше и без бокса ожидался отказ")
    finally:
        del os.environ["P40_L1_CACHE"]


def test_fetch_aux_cache_and_purge(tmp):   # TK-522: parquet из <кэш>/raw берётся с диска, с бокса просят только недостающее; после DONE файлы стираются
    cache = tmp / "cache" / "raw"
    fs = orch.box_files("binance", "XUSDT", "2026-10-05", aux_only=True, trades=True)
    assert len(fs) == 24 * 4
    for f in fs[:3]:
        (cache / f).parent.mkdir(parents=True, exist_ok=True)
        (cache / f).write_bytes(b"x" * 7)
    real, asked = subprocess.call, []

    def call(cmd, **kw):
        if any("box:" in str(c) for c in cmd):   # бокс: запомнить, что просили
            asked.extend(open(next(c for c in cmd if c.startswith("--files-from=")).split("=", 1)[1]).read().split())
            return 0
        return real(cmd, **kw)
    orch.subprocess = types.SimpleNamespace(**dict({k: getattr(subprocess, k) for k in dir(subprocess) if not k.startswith("__")}, call=call))
    os.environ["P40_L1_CACHE"] = str(tmp / "cache")
    try:
        n = orch.fetch(fs, str(tmp / "raw"), 2, str(tmp / "log"))
        assert n == 21 and all((tmp / "raw" / f).exists() for f in fs[:3])
        assert sorted(asked) == sorted(fs[3:])
        orch.purge_aux_cache({("binance", "XUSDT"): 1}, ["2026-10-05"])
        assert not any((cache / f).exists() for f in fs)
    finally:
        del os.environ["P40_L1_CACHE"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                fake_rsync(pathlib.Path(d))
                fn(pathlib.Path(d)) if fn.__code__.co_argcount else fn()
            print("ok", name)

