"""Тесты TK-530: выгрузка закрытых суток (day_upload.py). «Бокс» — локальная папка (LocalTransport)."""
import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
import day_upload as du  # noqa: E402

NOW = 1794463200.0  # 2026-11-12 06:00:00 UTC


def mk_day(src, day, files=None, age_s=7200):
    d = os.path.join(src, day)
    os.makedirs(d, exist_ok=True)
    for n, data in (files or {"book00.zst": b"a" * 100, "trade00.zst": b"b" * 50}).items():
        p = os.path.join(d, n)
        with open(p, "wb") as f:
            f.write(data)
        os.utime(p, (NOW - age_s, NOW - age_s))
    return d


def args(tmp, **kw):
    base = dict(exchange="binance", src=str(tmp / "src"), quiet_s=300.0, box_dir="alpha/collect",
                state=str(tmp / "state"), health=str(tmp / "health"), prune_free_gb=None)
    base.update(kw)
    return argparse.Namespace(**base)


def mkbox(tmp):
    (tmp / "src").mkdir()
    (tmp / "box").mkdir()
    return du.LocalTransport(str(tmp / "box"))


def health(tmp):
    with open(tmp / "health" / "upload-binance.json") as f:
        return json.load(f)


def test_closed_days_skips_today_fresh_and_empty(tmp_path):
    src = str(tmp_path)
    mk_day(src, "2026-11-10")
    mk_day(src, "2026-11-11", age_s=60)  # файл свежее quiet_s
    mk_day(src, "2026-11-12")  # сегодня UTC
    os.makedirs(os.path.join(src, "2026-11-09"))  # пусто
    os.makedirs(os.path.join(src, "junk"))
    assert du.closed_days(src, NOW, 300) == ["2026-11-10"]


def test_upload_verifies_and_repeat_is_noop(tmp_path):
    tr = mkbox(tmp_path)
    mk_day(str(tmp_path / "src"), "2026-11-11")
    a = args(tmp_path)
    assert du.run(a, tr, NOW) == 0
    box = tmp_path / "box" / "alpha/collect/binance/2026-11-11"
    assert sorted(os.listdir(box)) == ["SHA256SUMS", "UPLOADED.json", "book00.zst", "trade00.zst"]
    h = health(tmp_path)
    assert (h["last_ok_day"], h["lag_days"], h["last_error"], h["last_bytes"]) == ("2026-11-11", 0, None, 150)
    m = os.stat(box / "book00.zst").st_mtime_ns
    assert du.run(a, tr, NOW) == 0  # повтор: без перекопирования
    assert os.stat(box / "book00.zst").st_mtime_ns == m
    assert health(tmp_path)["last_bytes"] == 0


def test_sha_mismatch_is_failure_and_visible(tmp_path):
    class Corrupt(du.LocalTransport):
        def push(self, local_dir, rel, names):
            super().push(local_dir, rel, names)
            if "book00.zst" in names:
                with open(os.path.join(self.root, rel, "book00.zst"), "ab") as f:
                    f.write(b"x")

    tr = Corrupt(str(tmp_path / "box"))
    (tmp_path / "src").mkdir()
    (tmp_path / "box").mkdir()
    mk_day(str(tmp_path / "src"), "2026-11-11")
    assert du.run(args(tmp_path), tr, NOW) == 1
    h = health(tmp_path)
    assert "sha на боксе" in h["last_error"] and h["pending_days"] == ["2026-11-11"] and h["lag_days"] == 1
    assert not os.path.exists(tmp_path / "state" / "binance" / "2026-11-11.ok")


def test_transport_failure_stops_in_order_and_resumes(tmp_path):
    class Flaky(du.LocalTransport):
        fail = True

        def push(self, local_dir, rel, names):
            if self.fail:
                raise du.UploadError("rsync rc=30")
            super().push(local_dir, rel, names)

    tr = Flaky(str(tmp_path / "box"))
    (tmp_path / "src").mkdir()
    (tmp_path / "box").mkdir()
    mk_day(str(tmp_path / "src"), "2026-11-10")
    mk_day(str(tmp_path / "src"), "2026-11-11")
    a = args(tmp_path)
    assert du.run(a, tr, NOW) == 1
    assert health(tmp_path)["pending_days"] == ["2026-11-10", "2026-11-11"]
    tr.fail = False
    assert du.run(a, tr, NOW) == 0
    h = health(tmp_path)
    assert (h["last_ok_day"], h["lag_days"], h["last_error"]) == ("2026-11-11", 0, None)


def test_changed_after_verification_is_reuploaded(tmp_path):
    tr = mkbox(tmp_path)
    d = mk_day(str(tmp_path / "src"), "2026-11-11")
    a = args(tmp_path)
    du.run(a, tr, NOW)
    with open(os.path.join(d, "book00.zst"), "ab") as f:
        f.write(b"more")
    os.utime(os.path.join(d, "book00.zst"), (NOW - 7200, NOW - 7200))
    assert not du.is_done(str(tmp_path / "state" / "binance"), "2026-11-11", d)
    assert du.run(a, tr, NOW) == 0
    box_book = tmp_path / "box" / "alpha/collect/binance/2026-11-11/book00.zst"
    assert box_book.read_bytes() == open(os.path.join(d, "book00.zst"), "rb").read()


def test_prune_only_verified_unchanged_and_only_below_threshold(tmp_path):
    tr = mkbox(tmp_path)
    src = str(tmp_path / "src")
    mk_day(src, "2026-11-10")
    d11 = mk_day(src, "2026-11-11")
    a = args(tmp_path)
    du.run(a, tr, NOW)
    # без флага — не удаляет никогда
    assert os.path.isdir(os.path.join(src, "2026-11-10"))
    # порог ниже свободного — не удаляет
    assert du.prune(src, str(tmp_path / "state" / "binance"), 0) == []
    # изменённые после сверки сутки не удаляются, неизменные — удаляются (старые первыми)
    with open(os.path.join(d11, "book00.zst"), "ab") as f:
        f.write(b"z")
    gone = du.prune(src, str(tmp_path / "state" / "binance"), 1e12)
    assert gone == ["2026-11-10"] and os.path.isdir(d11)
    # день без .ok не удаляется
    d12 = mk_day(src, "2026-11-09")
    assert du.prune(src, str(tmp_path / "state" / "binance"), 1e12) == []
    assert os.path.isdir(d12)
