#!/usr/bin/env python3
"""П-09 (TK-014): Г-55 правилом выхода — один `--cells` на сутки через `alpha-gridq`, авг + сен + июль.

Клетки (`docs/research/P-09-wall-eat-exit.md` §4): базы B1 (Г-85б) и M (главный В-104) + по 6 форм выхода
`weat<X>s60{m|l|a}5`, X ∈ {50, 20} на каждой = 14 клеток; ход BTC — `--btc-minutes` e-jul + e-aug (§4).
Сборка задания — `p07-cells.build_job` (те же `COMMON`, σ своего дома, раскладка по `b5/<клетка>/<сутки>`).

    python3 p09-cells.py --bin bin/alpha-<хеш> --status
    python3 p09-cells.py --bin bin/alpha-<хеш> --submit --only-day 2026-08-03   # ворота §7 п. 2 до остального
    python3 p09-cells.py --bin bin/alpha-<хеш> --submit
"""
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
_spec = importlib.util.spec_from_file_location("p07cells", os.path.join(HERE, "p07-cells.py"))
pc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pc)

A = pc.A
JUL_HOME = f"{A}/epochs/e-jul"
HOMES = pc.HOMES + [("jul", JUL_HOME, [f"2026-07-{d:02d}" for d in range(1, 32)])]
SIGMA = {"aug": pc.SIGMA_FROM, "sep1": pc.SIGMA_FROM, "sep2": pc.SIGMA_FROM, "jul": f"{JUL_HOME}/study/sigma240"}
BTC = (f"--btc-minutes {JUL_HOME}/study/regime/ref-BTCUSDT-1m.csv "
       f"--btc-minutes {A}/epochs/e-aug/study/regime/ref-BTCUSDT-1m.csv")
ENTRY_M = "ladder3x2..20w2"


def cells():
    out = [pc.cell("p09-b-base", pc.ENTRY_B), pc.cell("p09-m-base", ENTRY_M)]
    for tag, entry in (("b", pc.ENTRY_B), ("m", ENTRY_M)):
        for x in (50, 20):
            for mode in "mla":
                out.append(pc.cell(f"p09-{tag}-{mode}{x}", entry, exit_form=f"weat{x}s60{mode}5"))
    return out


def submit(bname, home, day, todo, mem_gb):
    """Как `pc.submit`, но свой тег/лог и свой временный каталог (`.cellstmp-p09-`) — не пересекается с P-07 в том же доме."""
    script = pc.build_job(bname, home, day, todo).replace("b5/.cellstmp-", "b5/.cellstmp-p09-")
    cmd = ["bin/q-add.sh", "--tag", "p09-cells", "--mem-gb", str(mem_gb), "--home", home,
           "--log", f"tmp-p09/cells/{bname}-{day}.log", "--", "bash", "-c", script]
    out = subprocess.run(cmd, cwd=A, capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


def main():
    args = sys.argv[1:]
    if "--bin" not in args:
        sys.exit("нужен --bin bin/alpha-<хеш> (бинарник TK-014 после гейта)")
    b = args[args.index("--bin") + 1]
    pc.BIN = b if b.startswith("/") else f"{A}/{b}"
    pc.COMMON += " " + BTC
    pc.CELLS_DIR = f"{A}/tmp-p09/cells"
    os.makedirs(pc.CELLS_DIR, exist_ok=True)
    do_submit = "--submit" in args
    only = args[args.index("--only-day") + 1] if "--only-day" in args else None
    n = 0
    for bname, home, days in HOMES:
        for day in days:
            if only and day != only:
                continue
            if bname == "jul" and not os.path.exists(f"{home}/study/approaches/D20/{day}/.done"):
                continue
            todo = [c for c in cells() if not pc.day_done(home, c[0], day)]
            mark = f"{pc.CELLS_DIR}/{bname}-{day}.queued"
            if not todo or os.path.exists(mark):
                continue
            n += 1
            if do_submit:
                pc.SIGMA_FROM = SIGMA[bname]
                mem = pc.MEM_BIG_GB if (bname, day) in pc.BIG_DAYS else pc.MEM_NORMAL_GB
                jid = submit(bname, home, day, todo, mem)
                open(mark, "w").write(jid + "\n")
                print(f"{bname} {day}: {len(todo)} клеток -> {jid}")
    print(f"ИТОГО: {n} суток {'поставлено' if do_submit else 'к постановке'}")


if __name__ == "__main__":
    main()
