"""Прогон `test_health.py` без pytest (на srv его нет): каждая `test_*` вызывается с временным каталогом вместо `tmp_path`.
Запуск: python3 run_tests.py [каталог со страницей, по умолчанию рядом с файлом]; код возврата 1, если что-то упало."""
import importlib.util
import inspect
import pathlib
import sys
import tempfile
import traceback

d = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(d))
spec = importlib.util.spec_from_file_location("test_health", d / "test_health.py")
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)
ok = bad = 0
for name, fn in sorted(vars(m).items()):
    if not name.startswith("test_") or not callable(fn):
        continue
    with tempfile.TemporaryDirectory() as t:
        try:
            fn(pathlib.Path(t)) if inspect.signature(fn).parameters else fn()
            ok += 1
        except Exception:  # noqa: BLE001
            bad += 1
            print("FAIL", name)
            traceback.print_exc()
print(f"passed {ok} failed {bad}")
sys.exit(1 if bad else 0)
