"""Тесты выгрузки закрытых суток (day_upload.py). «Бокс» — локальная папка (LocalTransport)."""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
import day_upload as du  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
NOW = 1794463200.0  # 2026-11-12 06:00:00 UTC


def mk_day(root, day, files=None, age_s=7200, pool=True):
    d = os.path.join(root, day)
    os.makedirs(d, exist_ok=True)
    files = dict(files or {"book00-1.tsv.zst": b"a" * 100, "book00-1.tsv.zst.ok": b"ok\n", "trade00-1.tsv.zst": b"b" * 50})
    if pool:
        files["../pool-" + day + ".json"] = b'{"pool":[]}'
    for n, data in files.items():
        p = os.path.normpath(os.path.join(d, n))
        with open(p, "wb") as f:
            f.write(data)
        os.utime(p, (NOW - age_s, NOW - age_s))
    return d


def args(tmp, sources=("binance",), **kw):
    base = dict(source=[f"{s}={tmp / 'src' / s}" for s in sources], quiet_s=300.0, box_dir="alpha/collect",
                state=str(tmp / "state"), health=str(tmp / "health"), disk_path=str(tmp), free_alert_gb=0.0)
    base.update(kw)
    return argparse.Namespace(**base)


def mkbox(tmp, sources=("binance",)):
    for s in sources:
        (tmp / "src" / s).mkdir(parents=True)
    (tmp / "box").mkdir()
    return du.LocalTransport(str(tmp / "box"))


def health(tmp, ex="binance"):
    with open(tmp / "health" / f"upload-{ex}.json") as f:
        return json.load(f)


def snapshot(root):
    """Всё дерево источника: путь -> (размер, mtime_ns, sha256)."""
    out = {}
    for dp, _, fns in os.walk(root):
        for n in fns:
            p = os.path.join(dp, n)
            st = os.stat(p)
            out[os.path.relpath(p, root)] = (st.st_size, st.st_mtime_ns, du.sha256_file(p))
    return out


def test_closed_days_skips_today_fresh_and_empty(tmp_path):
    src = str(tmp_path)
    mk_day(src, "2026-11-10")
    mk_day(src, "2026-11-11", age_s=60)  # файл свежее quiet_s
    mk_day(src, "2026-11-12")  # сегодня UTC
    mk_day(src, "2026-11-13")  # будущее
    os.makedirs(os.path.join(src, "2026-11-09"))  # пусто
    os.makedirs(os.path.join(src, "junk"))
    assert du.closed_days(src, NOW, 300) == ["2026-11-10"]


def test_day_files_includes_pool_and_skips_dirs(tmp_path):
    src = str(tmp_path)
    d = mk_day(src, "2026-11-10")
    os.makedirs(os.path.join(d, "subdir"))
    assert sorted(du.day_files(src, "2026-11-10")) == ["book00-1.tsv.zst", "book00-1.tsv.zst.ok", "pool-2026-11-10.json", "trade00-1.tsv.zst"]


def test_upload_verifies_and_repeat_is_noop(tmp_path):
    tr = mkbox(tmp_path)
    mk_day(str(tmp_path / "src" / "binance"), "2026-11-11")
    a = args(tmp_path)
    assert du.run(a, tr, NOW) == 0
    box = tmp_path / "box" / "alpha/collect/binance/2026-11-11"
    assert sorted(os.listdir(box)) == ["SHA256SUMS", "UPLOADED.json", "book00-1.tsv.zst", "book00-1.tsv.zst.ok",
                                      "pool-2026-11-11.json", "trade00-1.tsv.zst"]
    h = health(tmp_path)
    assert (h["last_ok_day"], h["lag_days"], h["last_error"], h["last_bytes"]) == ("2026-11-11", 0, None, 100 + 3 + 50 + 11)
    m = os.stat(box / "book00-1.tsv.zst").st_mtime_ns
    assert du.run(a, tr, NOW) == 0  # повтор: без перекопирования
    assert os.stat(box / "book00-1.tsv.zst").st_mtime_ns == m
    assert health(tmp_path)["last_bytes"] == 0


def test_multi_source_one_dir_per_exchange_per_day(tmp_path):
    tr = mkbox(tmp_path, ("binance", "okx"))
    mk_day(str(tmp_path / "src" / "binance"), "2026-11-11")
    mk_day(str(tmp_path / "src" / "okx"), "2026-11-10")
    assert du.run(args(tmp_path, ("binance", "okx")), tr, NOW) == 0
    assert (tmp_path / "box" / "alpha/collect/binance/2026-11-11/UPLOADED.json").is_file()
    assert (tmp_path / "box" / "alpha/collect/okx/2026-11-10/UPLOADED.json").is_file()
    assert health(tmp_path, "okx")["last_ok_day"] == "2026-11-10"


def test_failure_in_one_exchange_does_not_block_others(tmp_path):
    tr = mkbox(tmp_path, ("binance", "okx"))
    mk_day(str(tmp_path / "src" / "okx"), "2026-11-10")
    a = args(tmp_path, ("binance", "okx"))
    a.source.insert(0, "bingx=" + str(tmp_path / "nope"))
    assert du.run(a, tr, NOW) == 1
    assert health(tmp_path, "okx")["last_ok_day"] == "2026-11-10"


def test_sha_mismatch_is_failure_and_visible(tmp_path):
    class Corrupt(du.LocalTransport):
        def push(self, paths, rel):
            super().push(paths, rel)
            if any(os.path.basename(p) == "book00-1.tsv.zst" for p in paths):
                with open(os.path.join(self.root, rel, "book00-1.tsv.zst"), "ab") as f:
                    f.write(b"x")

    tr = Corrupt(str(tmp_path / "box"))
    (tmp_path / "src" / "binance").mkdir(parents=True)
    (tmp_path / "box").mkdir()
    mk_day(str(tmp_path / "src" / "binance"), "2026-11-11")
    assert du.run(args(tmp_path), tr, NOW) == 1
    h = health(tmp_path)
    assert "sha на боксе" in h["last_error"] and h["pending_days"] == ["2026-11-11"] and h["lag_days"] == 1
    assert not os.path.exists(tmp_path / "state" / "binance" / "2026-11-11.ok")


def test_transport_failure_stops_in_order_and_resumes(tmp_path):
    class Flaky(du.LocalTransport):
        fail = True

        def push(self, paths, rel):
            if self.fail:
                raise du.UploadError("rsync rc=30")
            super().push(paths, rel)

    tr = Flaky(str(tmp_path / "box"))
    (tmp_path / "src" / "binance").mkdir(parents=True)
    (tmp_path / "box").mkdir()
    for d in ("2026-11-10", "2026-11-11"):
        mk_day(str(tmp_path / "src" / "binance"), d)
    a = args(tmp_path)
    assert du.run(a, tr, NOW) == 1
    assert health(tmp_path)["pending_days"] == ["2026-11-10", "2026-11-11"]
    tr.fail = False
    assert du.run(a, tr, NOW) == 0
    h = health(tmp_path)
    assert (h["last_ok_day"], h["lag_days"], h["last_error"]) == ("2026-11-11", 0, None)


def test_changed_after_verification_is_reuploaded(tmp_path):
    tr = mkbox(tmp_path)
    root = str(tmp_path / "src" / "binance")
    d = mk_day(root, "2026-11-11")
    a = args(tmp_path)
    du.run(a, tr, NOW)
    with open(os.path.join(d, "book00-1.tsv.zst"), "ab") as f:
        f.write(b"more")
    os.utime(os.path.join(d, "book00-1.tsv.zst"), (NOW - 7200, NOW - 7200))
    assert not du.is_done(str(tmp_path / "state" / "binance"), root, "2026-11-11")
    assert du.run(a, tr, NOW) == 0
    box_book = tmp_path / "box" / "alpha/collect/binance/2026-11-11/book00-1.tsv.zst"
    assert box_book.read_bytes() == open(os.path.join(d, "book00-1.tsv.zst"), "rb").read()


# ---- гарантия «ничего не удаляется» (предпочтение 3) ----

def test_no_delete_sources_untouched_on_success_and_failure(tmp_path):
    root = str(tmp_path / "src" / "binance")
    tr = mkbox(tmp_path)
    for d in ("2026-11-09", "2026-11-10", "2026-11-11"):
        mk_day(root, d)
    mk_day(root, "2026-11-12")  # сегодня
    before = snapshot(root)
    a = args(tmp_path, free_alert_gb=1e9)  # диск «почти полон»: всё равно ничего не удаляется
    assert du.run(a, tr, NOW) == 0
    assert snapshot(root) == before

    class Corrupt(du.LocalTransport):
        def push(self, paths, rel):
            super().push(paths, rel)
            raise du.UploadError("rsync rc=12")

    (tmp_path / "state2").mkdir()
    a2 = args(tmp_path, state=str(tmp_path / "state2"))
    assert du.run(a2, Corrupt(str(tmp_path / "box2")), NOW) == 1
    assert snapshot(root) == before


def test_no_delete_in_source_code():
    with open(os.path.join(HERE, "day_upload.py"), encoding="utf-8") as f:
        code = f.read()
    code = re.sub(r'""".*?"""', "", code, count=1, flags=re.S)  # докстринг описывает запреты словами
    forbidden = [r"rmtree", r"os\.remove", r"os\.unlink", r"\.unlink\(", r"os\.rmdir", r"removedirs", r"shutil\.move",
                 r"os\.rename", r"--delete", r"--remove", r"--prune", r"\bprune\b", r"\brm\b", r"truncate", r"os\.ftruncate"]
    hits = [p for p in forbidden if re.search(p, code)]
    assert hits == []
    # единственный os.replace — подмена своего .tmp (state/health)
    assert code.count("os.replace(") == 1 and "tmp = path + \".tmp\"" in code


def test_rsync_command_has_no_delete_flags():
    tr = du.SshTransport("box", bwlimit=20000)
    cmd = tr.push_cmd(["/data/collect/binance/data/2026-11-12/a", "/x/b"], "alpha/collect/binance/2026-11-12")
    assert cmd[0] == "rsync" and "--bwlimit=20000" in cmd
    assert not [c for c in cmd if c.startswith(("--delete", "--remove", "--prune", "--move"))]
    assert cmd[-1] == "box:alpha/collect/binance/2026-11-12/"


def test_service_is_read_only_for_collector_data():
    with open(os.path.join(HERE, "collector-day-upload.service"), encoding="utf-8") as f:
        unit = f.read()
    assert re.search(r"^ProtectSystem=strict$", unit, re.M)
    rw = re.search(r"^ReadWritePaths=(.*)$", unit, re.M).group(1).split()
    assert sorted(rw) == ["/data/health", "/data/upload-state"]
    srcs = re.findall(r"--source \w+=(\S+)", unit)
    assert len(srcs) == 4
    for s in srcs:
        assert not any(s == w or s.startswith(w + "/") or w.startswith(s + "/") for w in rw)
    assert re.search(r"^Nice=19$", unit, re.M) and re.search(r"^IOSchedulingClass=idle$", unit, re.M)


def test_free_space_alert(tmp_path, capsys):
    tr = mkbox(tmp_path)
    a = args(tmp_path, free_alert_gb=1e9)
    assert du.run(a, tr, NOW) == 0
    assert "ALERT" in capsys.readouterr().err
    h = health(tmp_path)
    assert h["disk_alert"] is True and h["disk_free_gb"] > 0
    a0 = args(tmp_path, free_alert_gb=0.0)
    du.run(a0, tr, NOW)
    assert "ALERT" not in capsys.readouterr().err and health(tmp_path)["disk_alert"] is False
