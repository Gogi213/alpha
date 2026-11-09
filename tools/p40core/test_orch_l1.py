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


def test_l1_leg_flags():
    a = types.SimpleNamespace(raw_legs=False)
    assert orch.l1_leg(a, "bybit") and orch.l1_leg(a, "gate") and not orch.l1_leg(a, "okx") and not orch.l1_leg(a, "binance")
    assert not orch.l1_leg(types.SimpleNamespace(raw_legs=True), "bybit")


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                fake_rsync(pathlib.Path(d))
                fn(pathlib.Path(d)) if fn.__code__.co_argcount else fn()
            print("ok", name)
