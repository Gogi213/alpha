#!/usr/bin/env python3
"""TK-015, П-07 (правило Судьи TK-010 `413da17`, dc250dc): чтение одного месяца января–июня — тот же путь, что
`p07-jul-read.py` (импорт без копирования, подмена глобалов модуля на месяц):

    main    b5/p07m-main    главный В-104 — вердикт
    b-base  b5/p07b-base    база Г-85б — вердикт
    b-k1    b5/p07b-base + keep K<=1 — описание, против b-base (B1–B3 как в июле)
    a-fr1   b5/p07a-h2-fr1  Г-85а fr+1 — описание (базы Г-85а нет — стоп, без пары)

Монета без минутных свечей (dc250dc, «то же правило — для января–июня»): сутки, где `n_no_sigma` > 1 % без неё не
были бы стопом, а у монеты в эти сутки есть сигналы без σ и нет ни одной строки σ₂₄₀ в этих сутках (свечей нет) —
монета вон из всех форм на весь месяц (`--drop`, как TRX); ворота пересчитываются без неё. `--data-gate` печатает
такие монеты по имени (без долей).

    python3 p07-h1-read.py --month jan --data-gate
    python3 p07-h1-read.py --month jan --out ~/alpha/tmp-p07/h1-read/jan.json
"""
import calendar
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun"]


def load_jul():
    for d in (HERE, os.path.expanduser("~/alpha/tmp-p07")):
        p = os.path.join(d, "p07-jul-read.py")
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("p07julread", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    raise SystemExit("нет p07-jul-read.py")


def sigma_days(jr, sym):
    """Сутки (YYYY-MM-DD), в которых у монеты есть хоть одна строка σ₂₄₀."""
    import datetime as dt
    sp = os.path.join(jr.JUL_HOME, "study", "sigma240", f"sigma-{sym}.csv")
    if not os.path.exists(sp):
        return set()
    with open(sp, encoding="utf-8") as fh:
        ms = {int(l.split(",", 1)[0]) // 86_400_000 for l in fh.read().splitlines()[1:] if l[:1].isdigit()}
    return {dt.datetime.fromtimestamp(d * 86400, dt.timezone.utc).strftime("%Y-%m-%d") for d in ms}


def no_candle_coins(jr):
    """Монеты правила dc250dc: в сутки с флагом n_no_sigma есть сигналы без σ и нет σ₂₄₀ за сутки."""
    out = set()
    d20 = os.path.join(jr.JUL_HOME, "study", "approaches", "D20")
    for day in jr.JUL_DAYS:
        if "n_no_sigma > 1 % сигналов" not in jr.day_gate(day):
            continue
        for f in os.listdir(os.path.join(d20, day)):
            if not (f.startswith("approaches-") and f.endswith(".csv")):
                continue
            sym = f[len("approaches-"):-4]
            if sym in jr.DROP_SET or jr.dropped_no_sigma(sym, day)[0] == 0:
                continue
            if day not in sigma_days(jr, sym):
                out.add(sym)
    return sorted(out)


def setup(jr, mon):
    m = MONTHS.index(mon) + 1
    first = f"2026-{m:02d}-01"
    nxt = f"2026-{m + 1:02d}-01"
    home = os.path.join(jr.HOME, f"epochs/e-{mon}")
    jr.JUL_HOME = home
    jr.OUT_ROOT = os.path.join(jr.HOME, "tmp-p07/h1-read", mon)
    jr.JUL_DAYS = [f"2026-{m:02d}-{d:02d}" for d in range(1, calendar.monthrange(2026, m)[1] + 1)]
    jr.FORMS = [f for f in jr.FORMS if f[0] != "a-base"]
    jr.CELL_DIRS = sorted({c for _, c, _, _ in jr.FORMS})
    jr.PAIRS = [("b-k1", "b-base")]
    remap = {"2026-08-01": first, "2026-09-24": nxt, "2026-09-01": nxt}

    def patch(kn):
        orig = kn.ms
        kn.ms = lambda day: orig(remap.get(day, day))
        return orig
    jr.patch_july = patch

    jr.GRID_PREFIX = mon  # grid-лог суток: серия `p07-h1-cells.py` — `<мес>-<сутки>`
    return home


def main():
    args = sys.argv[1:]
    if "--month" not in args:
        raise SystemExit("нужен --month jan|feb|mar|apr|may|jun")
    i = args.index("--month")
    mon = args[i + 1]
    if mon not in MONTHS:
        raise SystemExit(f"месяц {mon}: только {MONTHS}")
    del args[i:i + 2]
    jr = load_jul()
    setup(jr, mon)
    extra = no_candle_coins(jr)
    if extra:
        jr.DROP = ",".join(sorted(jr.DROP_SET | set(extra)))
        jr.DROP_SET = set(jr.DROP.split(","))
        print(f"{mon}: монеты без минутных свечей (dc250dc) — вон на весь месяц: {' '.join(extra)}")
    sys.argv = [sys.argv[0]] + args
    jr.main()


if __name__ == "__main__":
    main()
