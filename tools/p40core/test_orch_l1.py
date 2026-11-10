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


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                fake_rsync(pathlib.Path(d))
                fn(pathlib.Path(d)) if fn.__code__.co_argcount else fn()
            print("ok", name)
