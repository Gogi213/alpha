#!/usr/bin/env python3
"""TK-022 (В-157): сценарии суток янв–июн — клетки jall июля (`p07-all-jul.py`) с подменой месяца; в очередь не ставит.
Один сценарий = один `bounce-grid --cells` на сутки со всеми клетками (В-136) + Г-86 отдельным проходом `touch`.
Пишет `~/alpha/tmp-p07/cells-by-day/jall-<мес>-<сутки>.{sh,txt}`; запуск — из дома `~/alpha/epochs/e-<мес>`.

    python3 bin/p07-all-month.py <jan|feb|mar|apr|may|jun|aug> [--days 2026-01-01,2026-01-02] [--merge] [--bin <имя>]

`--merge` (TK-029): три отрезка суток одним проходом — отрезок 1 + `--extra-runs` из команд отрезков 2 и 3 (общий декод суток);
`--bin` — имя бинарника в `bin/` вместо alpha-e74f200-v3 (для --merge нужен бинарник с --extra-runs).
"""
import calendar
import importlib.util
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("jall", os.path.join(HERE, "p07-all-jul.py"))
ja = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ja)
pc, T9 = ja.pc, ja.T9

MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct"], 1)}


OLDBIN = "bin/alpha-e74f200-v3"
NL = chr(10)


def merge_script(script, day, extra_path, binname):
    """Тот же разбор, что в tools/compute/tk029-gate3.sh (гейт sha OK, янв 01): 3 отрезка → один прогон."""
    segs, cur = [], None
    for ln in script.split(NL):
        if ln == "set -e":
            cur = []
            segs.append(cur)
        if cur is not None:
            cur.append(ln)
    if len(segs) != 3:
        raise SystemExit(f"{day}: ожидалось 3 отрезка, есть {len(segs)}")
    pre = f"{OLDBIN} lob bounce-grid "
    extra = []
    for k in (2, 3):
        c = segs[k - 1][2]
        if not c.startswith(pre):
            raise SystemExit(f"{day}: отрезок {k}: строка 3 не bounce-grid")
        c = re.sub(r" > b5/\S+ 2>&1$", "", c[len(pre):])
        extra.append(re.sub(r"--out-dir b5/\S+", f"--out-dir b5/.m{k}tmp-{day}", c))
    out = list(segs[0])
    out[2] = re.sub(r" > b5/", f" --extra-runs {extra_path} > b5/", out[2], count=1)
    for k, old in ((2, f".t9tmp-touch-{day}"), (3, f".cellstmp-{day}")):
        out += [ln.replace(old, f".m{k}tmp-{day}") for ln in segs[k - 1][3:] if not ln.startswith("cp ")]
    text = NL.join(out)
    return text.replace(OLDBIN, f"bin/{binname}"), NL.join(extra) + NL


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
    ja.j9_owned = lambda *a: False  # метки j9-*.queued с деки (июль) отдавали клетки j9 «чужому заданию» → 2 отрезка вместо 3
    total = 0
    for day in days:
        old, appr, touch = ja.cells_for(mon, home, day)
        script = ja.build_job(mon, home, day, old, appr, touch)
        sh = f"{pc.CELLS_DIR}/jall-{mon}-{day}.sh"
        if "--merge" in args:
            binname = args[args.index("--bin") + 1] if "--bin" in args else "alpha-e74f200-v3"
            extra_path = f"{pc.CELLS_DIR}/jall-{mon}-{day}.extra.txt"
            script, extra = merge_script(script, day, extra_path, binname)
            with open(extra_path, "w", newline=NL) as f:
                f.write(extra)
        with open(sh, "w", newline="\n") as f:
            f.write(script)
        k = len(old) + len(appr) + len(touch)
        total += k
        print(f"{mon} {day}: {k} клеток (+Г-86) -> {sh}")
    print(f"ИТОГО: {len(days)} суток, {total} клетка-суток")


if __name__ == "__main__":
    main()
