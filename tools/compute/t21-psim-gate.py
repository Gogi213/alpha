#!/usr/bin/env python3
"""Гейт «до = после» для переноса правил портфеля `portfolio-sim.py` → `_lib/portfolio.py` (T-21, шаг
после «`_lib` модулем», `docs/findings/t21-metrics-canon-2026-09-27.md` §3 п.5). Формулы не менялись —
этот скрипт проверяет, что перенос не изменил ни одного числа.

Без аргументов — синтетика (детерминированная, без сети и без деки): строит сетку кругов по нескольким
монетам с пересечениями сделок, стопами/тейками/горизонтом, BTC-рядом для выключателя, сутками через
границу месяца 31.08→01.09, исключённой монетой и серией убытков; сравнивает СТАРЫЙ `portfolio-sim.py`
(`git show <--old-ref, по умолчанию HEAD>:tools/compute/portfolio-sim.py`) и ТЕКУЩИЙ файл по матрице
защит — `load_run`/`load_rounds` и `simulate()`. Печатает `OK`/`DIFF` на каждый случай.

Полный побайтный гейт на реальных прогонах — на Steam Deck, автором задачи, через `--real`:

    python3 t21-psim-gate.py --real --epoch история=epochs/e-archive:b5/titrc-u500-trail \\
        --variant кандидат=t-bid-btc1h-q1/ladder3x2..20w2-pct2-tr1x1-14400-ttl1800 \\
        --deposit-usd 2500 --position-usd 500 --json out

запускает СТАРЫЙ и НОВЫЙ `portfolio-sim.py` с одними и теми же ARGS (после `--real`) и сравнивает JSON
(`--json out` → каждый добавляет суффикс `-old`/`-new` сам этот скрипт, см. `run_real`).
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import importlib.util
import itertools
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent  # tools/compute/.. /.. = корень репо
NEW_PATH = HERE / "portfolio-sim.py"
MIN_MS = 60_000
BASE_DAY = dt.date(2026, 8, 30)  # день 0; день 1 = 31.08, день 2 = 01.09 (граница месяца), день 3 = 02.09


def ts_ns(day_offset: int, minute_of_day: int) -> int:
    d = BASE_DAY + dt.timedelta(days=day_offset)
    base_ms = int(dt.datetime(d.year, d.month, d.day, tzinfo=dt.timezone.utc).timestamp() * 1000)
    return (base_ms + minute_of_day * MIN_MS) * 1_000_000


def minute_ms(day_offset: int, minute_of_day: int) -> int:
    return ts_ns(day_offset, minute_of_day) // 1_000_000


def _load(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, str(path))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# коммит перед переносом правил в _lib/portfolio.py (e7e8379): «старое» закреплено, HEAD после переноса — уже новое
PRE_PORT_REF = "e7e8379~1"


def old_module_path(tmp_dir: Path, old_ref: str) -> Path:
    """Старый `portfolio-sim.py` — `git show <old_ref>:tools/compute/portfolio-sim.py`, во временный
    файл. По умолчанию `old_ref=HEAD` — верно, пока этот гейт запущен ДО коммита переноса (в этой
    сессии). После коммита `HEAD` уже несёт новую версию — на деке или позже вызывать с явным
    `--old-ref <хеш коммита перед переносом>`."""
    out = tmp_dir / "portfolio-sim-old.py"
    src = subprocess.run(["git", "show", f"{old_ref}:tools/compute/portfolio-sim.py"], cwd=str(REPO),
                          capture_output=True, check=True, encoding="utf-8").stdout
    out.write_text(src, encoding="utf-8")
    return out


# ---------------------------------------------------------------------------------------------------
# синтетическая сетка кругов
# ---------------------------------------------------------------------------------------------------

ROUND_HEADER = ["symbol", "form", "t0_ns", "exit_ns", "dir", "entry_px", "entry_vwap", "exit_px", "qty",
                "net_bps", "reason", "fill_frac"]


def build_rows():
    """Круги нескольких монет: перекрытие (BBBUSDT), выключатель BTC (CCCUSDT), исключённая монета
    (FFFUSDT), серия убытков через границу месяца (EEEUSDT), дневной стоп (GGGUSDT), запас для
    потолка позиций (все монеты дня 0 открыты почти одновременно)."""
    rows = []

    def add(sym, day, t0m, t1m, dir_, entry, exit_px, qty, net_bps, reason, entry_vwap=None, fill=1.0):
        rows.append([sym, "F1", ts_ns(day, t0m), ts_ns(day, t1m), dir_, entry, entry_vwap or entry,
                     exit_px, qty, net_bps, reason, fill])

    # AAAUSDT: обычные прибыль/убыток, без перекрытий
    add("AAAUSDT", 0, 60, 180, 1, 1.00, 1.02, 1000, 150.0, "take")
    add("AAAUSDT", 0, 240, 300, 1, 1.00, 0.98, 1000, -220.0, "stop")

    # BBBUSDT: второй вход, пока первый ещё открыт → «занята»
    add("BBBUSDT", 0, 60, 600, 1, 2.00, 2.01, 500, 40.0, "horizon")
    add("BBBUSDT", 0, 300, 650, 1, 2.00, 2.05, 500, 90.0, "take")

    # CCCUSDT: выключатель BTC (реагирует на срабатывание BTC-ряда на 130-й минуте дня 0); reason
    # "take" — принудительное закрытие доплачивает TAKER_LEG_BPS − MAKER_LEG_BPS
    add("CCCUSDT", 0, 90, 1000, 1, 1.50, 1.55, 500, 60.0, "take")

    # DDDUSDT: обычная монета для заполнения потолка позиций (день 0, утро)
    add("DDDUSDT", 0, 70, 400, 1, 3.00, 3.03, 400, 30.0, "stop")

    # EEEUSDT: серия убытков подряд в августе (день 0), потом сентябрь (день 2) — новый месяц, счёт заново
    add("EEEUSDT", 0, 0, 50, 1, 1.00, 0.95, 800, -180.0, "stop")
    add("EEEUSDT", 0, 60, 110, 1, 1.00, 0.96, 800, -150.0, "stop")
    add("EEEUSDT", 0, 120, 170, 1, 1.00, 0.97, 800, -120.0, "stop")  # 3-й подряд — стоп серии (streak_stop=3)
    add("EEEUSDT", 0, 180, 230, 1, 1.00, 0.90, 800, -400.0, "stop")  # должен быть пропущен «серией»
    add("EEEUSDT", 2, 60, 120, 1, 1.00, 1.05, 800, 220.0, "take")  # сентябрь — счёт серии заново

    # FFFUSDT: исключённая монета (набор «тест») — сделки есть, но не считаются при exclude
    add("FFFUSDT", 1, 30, 90, 1, 1.00, 0.80, 600, -1000.0, "stop")

    # GGGUSDT: дневной стоп — крупный убыток утром 31.08, второй вход тем же днём должен быть
    # пропущен «днём» при --day-stop-pct
    add("GGGUSDT", 1, 0, 60, 1, 1.00, 0.70, 2000, -1500.0, "stop")
    add("GGGUSDT", 1, 120, 180, 1, 1.00, 1.02, 2000, 90.0, "take")

    # HHHUSDT: дедлайн/горизонт на границе месяца (31.08 → 01.09) и фандинг (симметрично AAAUSDT)
    add("HHHUSDT", 1, 1400, 1460 + 1440, 1, 1.00, 1.01, 900, 45.0, "horizon")
    return rows


def write_rows(home: Path, run: str, set_name: str, rows):
    by_day = {}
    for r in rows:
        day = dt.datetime.fromtimestamp(r[2] / 1e9, dt.timezone.utc).strftime("%Y-%m-%d")
        by_day.setdefault(day, []).append(r)
    for day, day_rows in by_day.items():
        d = home / run / day / set_name
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "rounds.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(ROUND_HEADER)
            w.writerows(day_rows)


def build_btc_series():
    """(minutes_ms, vals_bps): срабатывание выключателя −999 bps на 130-й минуте дня 0 (после входа
    CCCUSDT на 90-й минуте) — известна к началу минуты (T-20 п.2)."""
    pts = [(minute_ms(0, 0), 0.0), (minute_ms(0, 100), 0.0), (minute_ms(0, 130), -999.0),
           (minute_ms(0, 131), -999.0), (minute_ms(1, 0), 0.0), (minute_ms(2, 0), 0.0)]
    pts.sort()
    return [m for m, _ in pts], [v for _, v in pts]


def write_klines(kdir: Path):
    """Свечи минутного шага на 2 суток вперёд каждой монеты, достаточно для `minute_curve` (без дыр
    внутри интервалов сделок; шаг цены — небольшой синусоидальный ход, детерминированный)."""
    closes = {
        "AAAUSDT": 1.00, "BBBUSDT": 2.00, "CCCUSDT": 1.50, "DDDUSDT": 3.00,
        "EEEUSDT": 1.00, "GGGUSDT": 1.00, "HHHUSDT": 1.00,
    }
    kdir.mkdir(parents=True, exist_ok=True)
    for sym, base in closes.items():
        rows = []
        for day in range(4):
            for m in range(0, 1440, 5):
                wobble = 1.0 + 0.01 * ((m // 5) % 7 - 3) / 3.0
                rows.append((minute_ms(day, m), round(base * wobble, 6)))
        # свеча ровно на минуту срабатывания выключателя (CCCUSDT, день 0, минута 130) — форс-закрытие
        if sym == "CCCUSDT":
            rows.append((minute_ms(0, 130), 1.45))
        with open(kdir / f"ref-{sym}-1m.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["minute_ms", "close"])
            w.writerows(sorted(set(rows)))


def write_funding(fpath: Path):
    with open(fpath, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["symbol", "funding_time_ms", "funding_rate"])
        w.writerow(["AAAUSDT", minute_ms(0, 90), 0.0005])
        w.writerow(["BBBUSDT", minute_ms(0, 400), -0.0003])


MATRIX = [
    dict(max_pos=0, day_stop_pct=0.0, kill_bps=0.0, exclude=set(), streak_stop=0, funding=False, size_mult=1.0),
    dict(max_pos=3, day_stop_pct=0.0, kill_bps=0.0, exclude=set(), streak_stop=0, funding=False, size_mult=1.0),
    dict(max_pos=0, day_stop_pct=5.0, kill_bps=0.0, exclude=set(), streak_stop=0, funding=False, size_mult=1.0),
    dict(max_pos=0, day_stop_pct=0.0, kill_bps=150.0, exclude=set(), streak_stop=0, funding=False, size_mult=1.0),
    dict(max_pos=0, day_stop_pct=0.0, kill_bps=0.0, exclude={"FFFUSDT"}, streak_stop=0, funding=False, size_mult=1.0),
    dict(max_pos=0, day_stop_pct=0.0, kill_bps=0.0, exclude=set(), streak_stop=3, funding=False, size_mult=1.0),
    dict(max_pos=0, day_stop_pct=0.0, kill_bps=0.0, exclude=set(), streak_stop=0, funding=True, size_mult=1.0),
    dict(max_pos=0, day_stop_pct=0.0, kill_bps=0.0, exclude=set(), streak_stop=0, funding=False, size_mult=2.5),
    dict(max_pos=2, day_stop_pct=3.0, kill_bps=150.0, exclude={"FFFUSDT"}, streak_stop=3, funding=True,
         size_mult=1.5),
]


def _norm(r: dict) -> dict:
    r = dict(r)
    r.pop("_closes", None)  # мс закрытия — не пишется в --json у CLI, не входит в сравнение JSON-структур
    return r


def run_case(mod, home, run, set_name, kdir, fpath, case) -> dict:
    mod.SIZE_MULT = case["size_mult"]
    rows = mod.load_rounds(str(home), run, set_name, "F1")
    klines = mod.Klines([str(kdir)])
    funding = mod.Funding(str(fpath)) if case["funding"] else None
    btc = build_btc_series()
    r = mod.simulate(rows, btc, klines, 10_000.0, case["max_pos"], case["day_stop_pct"], case["kill_bps"],
                      case["exclude"], 59.7, case["streak_stop"], funding)
    return _norm(r)


def run_synthetic(old_ref: str) -> bool:
    ok = True
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        old_path = old_module_path(tmp, old_ref)
        old = _load("psim_old", old_path)
        new = _load("psim_new", NEW_PATH)

        home = tmp / "epoch"
        write_rows(home, "run1", "S1", build_rows())
        kdir = tmp / "klines"
        write_klines(kdir)
        fpath = tmp / "funding.csv"
        write_funding(fpath)

        for i, case in enumerate(MATRIX):
            r_old = run_case(old, home, "run1", "S1", kdir, fpath, case)
            r_new = run_case(new, home, "run1", "S1", kdir, fpath, case)
            j_old = json.dumps(r_old, sort_keys=True)
            j_new = json.dumps(r_new, sort_keys=True)
            same = j_old == j_new
            ok = ok and same
            print(f"[{i}] {case} -> {'OK' if same else 'DIFF'}")
            if not same:
                print(f"    old: {j_old}")
                print(f"    new: {j_new}")

        # load_run/load_rounds напрямую — байт-в-байт список строк (без прогона через simulate)
        for size_mult in (1.0, 2.0):
            old.SIZE_MULT = size_mult
            new.SIZE_MULT = size_mult
            lr_old = old.load_run(str(home), "run1", "S1", "F1")
            lr_new = new.load_run(str(home), "run1", "S1", "F1")
            same = json.dumps(lr_old, sort_keys=True) == json.dumps(lr_new, sort_keys=True)
            ok = ok and same
            print(f"[load_run size_mult={size_mult}] -> {'OK' if same else 'DIFF'}")
        old.SIZE_MULT = new.SIZE_MULT = 1.0
    return ok


# ---------------------------------------------------------------------------------------------------
# --real: CLI старого/нового portfolio-sim.py на реальных данных (Steam Deck)
# ---------------------------------------------------------------------------------------------------

def run_real(args: list, old_ref: str) -> bool:
    with tempfile.TemporaryDirectory() as td:
        tmp = Path(td)
        old_path = old_module_path(tmp, old_ref)
        out_old, out_new = tmp / "out-old.json", tmp / "out-new.json"

        def with_json_flag(a, out_path):
            a = list(a)
            if "--json" in a:
                a[a.index("--json") + 1] = str(out_path)
            else:
                a += ["--json", str(out_path)]
            return a

        cmd_old = [sys.executable, str(old_path)] + with_json_flag(args, out_old)
        cmd_new = [sys.executable, str(NEW_PATH)] + with_json_flag(args, out_new)
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        r_old = subprocess.run(cmd_old, cwd=str(REPO), capture_output=True, encoding="utf-8", env=env)
        r_new = subprocess.run(cmd_new, cwd=str(REPO), capture_output=True, encoding="utf-8", env=env)
        if r_old.returncode != 0 or r_new.returncode != 0:
            print("old rc:", r_old.returncode, r_old.stderr[-2000:])
            print("new rc:", r_new.returncode, r_new.stderr[-2000:])
            return False
        j_old = json.loads(out_old.read_text(encoding="utf-8"))
        j_new = json.loads(out_new.read_text(encoding="utf-8"))
        same = json.dumps(j_old, sort_keys=True) == json.dumps(j_new, sort_keys=True)
        print("--real ->", "OK" if same else "DIFF")
        if not same and r_old.stdout != r_new.stdout:
            print("stdout differs too (таблица)")
        return same


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old-ref", default=PRE_PORT_REF, help="git-ссылка на СТАРЫЙ portfolio-sim.py (до переноса)")
    ap.add_argument("--real", nargs=argparse.REMAINDER, help="ARGS… — прогон CLI на реальных данных вместо синтетики")
    a = ap.parse_args()
    ok = run_real(a.real, a.old_ref) if a.real is not None else run_synthetic(a.old_ref)
    print("ИТОГ:", "OK" if ok else "DIFF")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
