#!/usr/bin/env python3
"""TK-015, П-07 (правило Судьи TK-010 `413da17`): январь–июнь — замороженные формы одним `--cells` на сутки (В-136)
через `alpha-gridq`. Флаги, бинарник и клетки — `p07-cells.py`/`p07-jul-cells.py` без правок; база Г-85а — стоп
(клетки нет), K ≤ 1 — фильтр busy-replay по кэшу подходов над `p07b-base`, не клетка:

    p07m-main    главный В-104 `ladder3x2..20w2` (вердикт)
    p07b-base    база Г-85б (вердикт)
    p07a-h2-fr1  Г-85а `single@fr+1` (описание)

Ворот нет: бинарник и клетки те же, что прошли ворота 03.08 в TK-010. Эпохи `epochs/e-<мес>` (подготовка Инженера,
`epoch-box-prep.sh`), σ₂₄₀ — `epochs/e-<мес>/study/sigma240`. Сутки без `D20/<день>/.done` не ставятся — повторный
`--submit` доставит.

    python3 p07-h1-cells.py --status
    python3 p07-h1-cells.py --submit
"""
import calendar
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("p07jul", os.path.join(HERE, "p07-jul-cells.py"))
pj = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pj)
pc = pj.pc

A = pc.A
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun"]
MEM_GB = pj.MEM_GB
CELLS = [c for c in pj.CELLS if c[0] != "p07a-base"]


def home(mon):
    return f"{A}/epochs/e-{mon}"


def days(mon):
    m = MONTHS.index(mon) + 1
    return [f"2026-{m:02d}-{d:02d}" for d in range(1, calendar.monthrange(2026, m)[1] + 1)]


def jobs():
    """(имя серии, месяц, сутки, клетки) — только несделанное и с готовым кэшем подходов."""
    out = []
    for mon in MONTHS:
        h = home(mon)
        for day in days(mon):
            if not os.path.exists(f"{h}/study/approaches/D20/{day}/.done"):
                continue
            todo = [c for c in CELLS if not pc.day_done(h, c[0], day)]
            if todo:
                out.append((mon, day, todo))
    return out


def submit(mon, day, cells):
    """`pc.submit` с `--prio 4` — как подготовка tk015-prep Инженера (В-149: январь–июнь сейчас, впереди p07-t9/p08)."""
    script = pc.build_job(mon, home(mon), day, cells)
    cmd = ["bin/q-add.sh", "--tag", "p07-h1", "--prio", "4", "--mem-gb", str(MEM_GB),
           "--home", home(mon), "--log", f"tmp-p07/cells-by-day/{mon}-{day}.log", "--", "bash", "-c", script]
    out = subprocess.run(cmd, cwd=A, capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--status"
    n = 0
    for mon, day, cells in jobs():
        if pj.queued(mon, day):
            continue
        n += 1
        if mode == "--submit":
            pc.SIGMA_FROM = f"{home(mon)}/study/sigma240"
            jid = submit(mon, day, cells)
            open(f"{pc.CELLS_DIR}/{mon}-{day}.queued", "w").write(jid + "\n")
            print(f"{mon} {day}: {len(cells)} клеток -> {jid}")
        else:
            print(f"{mon} {day}: {len(cells)} клеток")
    print(f"ИТОГО: {n} суток к постановке")


if __name__ == "__main__":
    main()
