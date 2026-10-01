#!/usr/bin/env python3
"""TK-022 (В-157): сценарии суток янв–июн — клетки jall июля (`p07-all-jul.py`) с подменой месяца; в очередь не ставит.
Один сценарий = один `bounce-grid --cells` на сутки со всеми клетками (В-136) + Г-86 отдельным проходом `touch`.
Пишет `~/alpha/tmp-p07/cells-by-day/jall-<мес>-<сутки>.{sh,txt}`; запуск — из дома `~/alpha/epochs/e-<мес>`.

    python3 bin/p07-all-month.py <jan|feb|mar|apr|may|jun> [--days 2026-01-01,2026-01-02]
"""
import calendar
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("jall", os.path.join(HERE, "p07-all-jul.py"))
ja = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ja)
pc, T9 = ja.pc, ja.T9

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun"], 1)}


def main():
    mon = sys.argv[1]
    n = MONTHS[mon]
    args = sys.argv[2:]
    last = calendar.monthrange(2026, n)[1]
    days = [f"2026-{n:02d}-{d:02d}" for d in range(1, last + 1)]
    if "--days" in args:
        days = args[args.index("--days") + 1].split(",")
    home = f"{pc.A}/epochs/e-{mon}"
    pc.SIGMA_FROM = T9.P.SIGMA_FROM = f"{home}/study/sigma240"
    ja.cell_done = lambda *a: False  # деки уже считали 4 формы на части суток янв/мар/апр — на VPS пересчёт всех клеток (ворота)
    pc.day_done = lambda *a: False
    total = 0
    for day in days:
        old, appr, touch = ja.cells_for(mon, home, day)
        script = ja.build_job(mon, home, day, old, appr, touch)
        sh = f"{pc.CELLS_DIR}/jall-{mon}-{day}.sh"
        with open(sh, "w", newline="\n") as f:
            f.write(script)
        k = len(old) + len(appr) + len(touch)
        total += k
        print(f"{mon} {day}: {k} клеток (+Г-86) -> {sh}")
    print(f"ИТОГО: {len(days)} суток, {total} клетка-суток")


if __name__ == "__main__":
    main()
