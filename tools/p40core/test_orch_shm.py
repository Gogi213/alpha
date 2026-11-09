"""TK-510: бюджет /dev/shm в orch.py — брони, отказ по оценке, ENOSPC как отдельный сбой.
Запуск — на сервере счёта (Linux: orch.py берёт os.statvfs/wait4, на Windows их нет): python3 test_orch_shm.py"""
import importlib.util, os, tempfile, threading, time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("orch", os.path.join(HERE, "orch.py"))
orch = importlib.util.module_from_spec(spec)
spec.loader.exec_module(orch)   # main() под __main__-гвардом: импорт ничего не запускает

GB = 2 ** 30


def budget(cap_gb, tmp):
    return orch.ShmBudget(tmp, frac=1.0, total=cap_gb * GB, foreign=0)


def test_estimate_over_cap_refused(tmp_path):
    b = budget(46 * 8 // 10, str(tmp_path))
    try:
        b.set("a", 40 * GB, full=True)
    except orch.ShmFull as e:
        assert "chunk" in str(e)
    else:
        raise AssertionError("оценка 40 ГБ > бюджета 36,8 ГБ должна отказывать")


def test_second_full_job_waits_for_first(tmp_path):
    b = budget(40, str(tmp_path))
    b.set("a", 25 * GB, full=True)
    got = threading.Event()
    threading.Thread(target=lambda: (b.set("b", 25 * GB, full=True, wait=0.05), got.set())).start()
    time.sleep(0.3)
    assert not got.is_set(), "25+25 > 40: второе задание обязано ждать"
    b.release("a")
    assert got.wait(2), "после освобождения второе берёт бронь"


def test_pilots_do_not_deadlock(tmp_path):
    b = budget(40, str(tmp_path))
    b.set("a", 3 * GB)          # пилоты (full=False)
    b.set("b", 3 * GB)
    b.set("c", 38 * GB, full=True, wait=0.05)   # 3+3+38 > 40, но полных броней у других нет — идёт
    assert b.res["c"] == 38 * GB


def test_pilot_follows_full_when_room(tmp_path):
    b = budget(40, str(tmp_path))
    b.set("a", 20 * GB, full=True)
    b.set("b", 15 * GB, full=True)
    assert sum(b.res.values()) == 35 * GB


def test_peak_estimate():
    # kept1 — .bin одних суток, peak1 - kept1 — тяжёлое сырьё одних суток; K·kept1 + сырьё, с запасом
    assert orch.peak_estimate(1500, 1000, 14) == int((14 * 1000 + 500) * orch.SHM_MARGIN)


def test_nospace_from_log(tmp_path):
    log = tmp_path / "orch.log"
    log.write_text("thread panicked: Os { code: 28, kind: StorageFull, message: \"No space left on device\" }\n")
    assert orch.nospace(str(log))
    log.write_text("rsync: connection unexpectedly closed\n")
    assert not orch.nospace(str(log))
    assert not orch.nospace(str(tmp_path / "нет.log"))


def test_fail_raises_shmfull_only_on_nospace(tmp_path):
    log = tmp_path / "l"
    (tmp_path / "bin").mkdir()
    log.write_text("No space left on device (28)\n")
    try:
        orch.fail("pq2bin rc 101", str(log), str(tmp_path / "bin" / "x"))
    except orch.ShmFull:
        pass
    else:
        raise AssertionError("ENOSPC в логе -> ShmFull")
    log.write_text("другая ошибка\n")
    try:
        orch.fail("pq2bin rc 101", str(log), str(tmp_path / "bin" / "x"))
    except orch.ShmFull:
        raise AssertionError("не ENOSPC -> обычный сбой")
    except RuntimeError:
        pass


if __name__ == "__main__":
    import pathlib
    for name, fn in sorted(globals().items()):
        if name.startswith("test_"):
            with tempfile.TemporaryDirectory() as d:
                fn(pathlib.Path(d)) if fn.__code__.co_argcount else fn()
            print("ok", name)
