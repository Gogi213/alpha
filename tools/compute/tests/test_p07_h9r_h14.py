"""p07-h9r-h14.py: H9р (запрет повтора после неудачи) — фикстурный тест.

Проверяет ровно то, что требует протокол (`docs/research/P-07-g85-titration.md` §13, «Поправка 3»,
решение владельца через CEO `b275d84`):
  - неудача (`reason=='stop'` И цена выхода ниже цены входа) запрещает следующий сигнал ТОГО ЖЕ
    сетапа (здесь — S1, «та же стена»);
  - тот же провал НЕ действует на другую клетку (здесь — H14 `k3`, «первые 3 подхода»), потому что
    её правило не смотрит на исход круга вообще;
  - безубыточный/повышенный стоп (цена выхода ≥ цены входа) неудачей не считается и не запрещает.

Запуск: `python -m pytest tools/compute/tests -q`.
"""
from __future__ import annotations

import csv
import importlib.util
import os
from pathlib import Path

HERE = Path(__file__).resolve().parent
COMPUTE = HERE.parent
P07BASE_PATH = COMPUTE / "p07-h9h10.py"
H9R_PATH = COMPUTE / "p07-h9r-h14.py"

FORM = "single@fr-pct2-tr1x1-14400-ttl1800"
DAY = "2026-08-01"
SYMBOL = "AAAUSDT"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _write_signals(dir_, rows):
    """rows: [(signal_index, t0_ns, price_tick, idle_ns)] — по времени, один символ/сутки."""
    os.makedirs(dir_, exist_ok=True)
    with open(dir_ / "signals.csv", "w", newline="", encoding="utf-8") as f:
        f.write("# fixture\n")
        w = csv.writer(f)
        w.writerow(["symbol", "day_utc", "form", "signal_index", "t0_ns", "price_tick",
                    "entry_px", "step", "idle_ns", "residual", "exit_ns"])
        for idx, t0, pt, idle in rows:
            w.writerow([SYMBOL, DAY, FORM, idx, t0, pt, "1.0", "normal", idle, "", t0 + 100])


def _write_rounds(dir_, rows):
    """rows: [(signal_index, entry_px, exit_px, reason)]."""
    os.makedirs(dir_, exist_ok=True)
    with open(dir_ / "rounds.csv", "w", newline="", encoding="utf-8") as f:
        f.write("# fixture\n")
        w = csv.writer(f)
        w.writerow(["symbol", "day_utc", "form", "signal_index", "t0_ns", "dir", "entry_px",
                    "exit_px", "qty", "net_bps", "reason", "exit_ns"])
        for idx, entry_px, exit_px, reason in rows:
            w.writerow([SYMBOL, DAY, FORM, idx, 0, 1, entry_px, exit_px, 500,
                        (exit_px - entry_px) * 10000, reason, 1])


def _write_wall(dir_, rows):
    """rows: [(price_tick, birth_ms, arm_ms, approach_index)] — одна стена (bid), не снята."""
    day_dir = dir_ / DAY
    os.makedirs(day_dir, exist_ok=True)
    with open(day_dir / f"approaches-{SYMBOL}.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["side", "price_tick", "birth_ms", "arm_ms", "approach_index",
                    "disarm_ms", "disarm_reason"])
        for pt, birth, arm, ai in rows:
            w.writerow(["bid", pt, birth, arm, ai, "", "alive"])


def _build_home(tmp_path, entry_px, exit_px):
    """Два подхода к одной стене (price_tick=1000, birth_ms=500): первый — сигнал 0 (может дать
    неудачу по параметру), второй — сигнал 1 к ТОЙ ЖЕ стене после освобождения монеты."""
    home = tmp_path / "home"
    signals_dir = home / "b5" / "p07a-base" / DAY / "t-bid-btc4h-q1"
    arm0_ms, arm1_ms = 5_000, 20_000
    t0_0, t0_1 = arm0_ms * 1_000_000, arm1_ms * 1_000_000
    idle0_ns = (arm0_ms + 100) * 1_000_000  # свободна задолго до второго подхода
    _write_signals(signals_dir, [(0, t0_0, 1000, idle0_ns), (1, t0_1, 1000, idle0_ns + 1)])
    _write_rounds(signals_dir, [(0, entry_px, exit_px, "stop"), (1, 1.0, 1.05, "take")])
    _write_wall(home / "study/approaches/D20",
                [(1000, 500, arm0_ms, 0), (1000, 500, arm1_ms, 1)])
    return home


def test_s1_bans_repeat_at_same_wall_after_stop_loss(tmp_path, monkeypatch):
    p07base = _load(P07BASE_PATH, "p07base_s1")
    h9r = _load(H9R_PATH, "h9r_s1")
    home = _build_home(tmp_path, entry_px=1.0, exit_px=0.9)  # exit < entry -> неудача
    monkeypatch.setattr(p07base, "HOMES", {"test": str(home)})

    keep = h9r.simulate(p07base, "a", "s1")
    kept_indices = {t0 for (_sym, t0, _pt) in keep}
    assert kept_indices == {str(5_000 * 1_000_000)}, "второй подход к той же стене должен быть исключён"


def test_breakeven_stop_does_not_ban(tmp_path, monkeypatch):
    p07base = _load(P07BASE_PATH, "p07base_be")
    h9r = _load(H9R_PATH, "h9r_be")
    home = _build_home(tmp_path, entry_px=1.0, exit_px=1.0)  # exit == entry -> НЕ неудача (безубыток)
    monkeypatch.setattr(p07base, "HOMES", {"test": str(home)})

    keep = h9r.simulate(p07base, "a", "s1")
    kept_indices = {t0 for (_sym, t0, _pt) in keep}
    assert kept_indices == {str(5_000 * 1_000_000), str(20_000 * 1_000_000)}, \
        "безубыточный стоп не должен запрещать следующий подход"


def test_other_cell_ignores_stop_failure(tmp_path, monkeypatch):
    """H14 k3 («первые 3 подхода») не смотрит на исход круга вообще — тот же провал, что банит S1,
    не должен ничего вырезать в k3: оба сигнала (approach_index 0 и 1, оба < 3) остаются."""
    p07base = _load(P07BASE_PATH, "p07base_k3")
    h9r = _load(H9R_PATH, "h9r_k3")
    home = _build_home(tmp_path, entry_px=1.0, exit_px=0.9)  # тот же провал, что в первом тесте
    monkeypatch.setattr(p07base, "HOMES", {"test": str(home)})

    keep = h9r.simulate(p07base, "a", "k3")
    kept_indices = {t0 for (_sym, t0, _pt) in keep}
    assert kept_indices == {str(5_000 * 1_000_000), str(20_000 * 1_000_000)}, \
        "неудача другого сетапа (S1) не должна резать клетку k3"
