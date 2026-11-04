"""TK-490: обрезка интервалов вселенной до периода, дневные счётчики, самопроверка tk490_check, флаги умолчания закачки."""
import importlib.util, os, subprocess, sys
from datetime import date

HERE = os.path.dirname(os.path.abspath(__file__))
COMP = os.path.join(HERE, '..')


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(COMP, name + '.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_clip_and_daily():
    m = load('tk490_universe')
    ivs = [('A', date(2024, 12, 5), date(2025, 1, 3)), ('B', date(2024, 12, 5), date(2024, 12, 31)), ('C', date(2026, 11, 2), date(2026, 11, 9))]
    got = m.clip(ivs, date(2025, 1, 1), date(2026, 11, 3))
    assert got == [('A', date(2025, 1, 1), date(2025, 1, 3)), ('C', date(2026, 11, 2), date(2026, 11, 3))]
    cnt = m.daily_counts({'x': got[:1], 'y': got}, date(2025, 1, 1), date(2025, 1, 4))
    assert cnt[date(2025, 1, 3)] == {'x': 1, 'y': 1} and cnt[date(2025, 1, 4)] == {'x': 0, 'y': 0}


def test_check_selftest():
    r = subprocess.run([sys.executable, os.path.join(COMP, 'tk490_check.py'), '--selftest'], capture_output=True, text=True)
    assert r.returncode == 0 and 'selftest ok' in r.stdout, r.stdout + r.stderr


def test_defaults_unchanged():
    env = {k: v for k, v in os.environ.items() if not k.startswith(('TK450_', 'TK478_', 'K_'))}
    os.environ.clear(); os.environ.update(env)
    k = importlib.util.spec_from_file_location('k450', os.path.join(COMP, 'tk450-binance-klines.py'))
    mk = importlib.util.module_from_spec(k); k.loader.exec_module(mk)
    assert mk.FIRST == '2020-01'
    f = importlib.util.spec_from_file_location('f478', os.path.join(COMP, 'tk478-funding.py'))
    mf = importlib.util.module_from_spec(f); f.loader.exec_module(mf)
    assert mf.FROM_MS == 0 and mf.SYMFILE is None
    b = importlib.util.spec_from_file_location('b476', os.path.join(COMP, 'tk476-bybit-hist.py'))
    mb = importlib.util.module_from_spec(b); b.loader.exec_module(mb)
    assert mb.GZ == {} and mb.KA is False
