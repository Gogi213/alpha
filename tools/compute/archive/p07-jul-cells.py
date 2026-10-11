#!/usr/bin/env python3
"""TK-010, П-07 поправка 5 (Судья `0e4d73d`): июль — проверка замороженного списка одним `--cells` на сутки (В-136)
через `alpha-gridq`. Клетки (формы п. 1, флаги и бинарник — `p07-cells.py` без правок):

    p07m-main    главный В-104 `ladder3x2..20w2-pct2-tr1x1-14400-ttl1800` (как `b5/titrc-u500r`, busy-skip off → busy-replay)
    p07a-base    база Г-85а `single@fr`
    p07b-base    база Г-85б `ladder3x0..0.0409sw2` (K ≤ 1 — фильтр busy-replay по кэшу подходов, не клетка)
    p07a-h2-fr1  Г-85а `single@fr+1` (справочно)

Ворота до чтения (п. 2): те же клетки тем же вызовом на 03.08 в `epochs/e-aug/b5/<клетка>-julgate` — сверка побайтно
с имеющимися (`--gate`). σ₂₄₀ июля — `epochs/e-jul/study/sigma240` (`sigma-table.py`, TK-005); 03.08 — `study/sigma240`,
как в TK-004. Сутки без `D20/<день>/.done` не ставятся (подготовка июля ещё идёт) — повторный `--submit` доставит.

    python3 p07-jul-cells.py --status
    python3 p07-jul-cells.py --submit            # готовые сутки июля + 03.08 (ворота), уже поставленное/готовое — пропуск
    python3 p07-jul-cells.py --gate              # статус ворот 03.08: только «ok/РАЗНИЦА» по клеткам, без чисел
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
AUG_HOME = f"{A}/epochs/e-aug"
JUL_DAYS = [f"2026-07-{d:02d}" for d in range(1, 32)]
GATE_DAY = "2026-08-03"
SIGMA = {"jul": f"{JUL_HOME}/study/sigma240", "julgate": f"{A}/study/sigma240"}
MEM_GB = 3.0

CELLS = [
    pc.cell("p07m-main", "ladder3x2..20w2"),
    pc.cell("p07a-base", pc.ENTRY_A),
    pc.cell("p07b-base", pc.ENTRY_B),
    pc.cell("p07a-h2-fr1", "single@fr+1"),
]
# Ворота: с чем сверять клетку 03.08 (каталог на e-aug; у главного — прогон busy-skip on, строки формы tr1x1)
GATE_REF = {"p07m-main": "titrc-u500r", "p07a-base": "p07a-base", "p07b-base": "p07b-base", "p07a-h2-fr1": "p07a-h2-fr1"}


def gate_cells():
    return [(f"{c[0]}-julgate",) + c[1:] for c in CELLS]


def jobs():
    """(имя серии, дом, сутки, клетки) — только несделанное и с готовым кэшем подходов."""
    out = []
    todo = [c for c in gate_cells() if not pc.day_done(AUG_HOME, c[0], GATE_DAY)]
    if todo:
        out.append(("julgate", AUG_HOME, GATE_DAY, todo))
    for day in JUL_DAYS:
        if not os.path.exists(f"{JUL_HOME}/study/approaches/D20/{day}/.done"):
            continue
        todo = [c for c in CELLS if not pc.day_done(JUL_HOME, c[0], day)]
        if todo:
            out.append(("jul", JUL_HOME, day, todo))
    return out


def queued(bname, day):
    """Уже поставлено этим скриптом (лог задачи есть) — не ставить второй раз."""
    return os.path.exists(f"{pc.CELLS_DIR}/{bname}-{day}.queued")


def body(path):
    """Строки файла без строк `#` (шапка с флагами и метка busy-replay по п. 2 не сравниваются)."""
    return [l for l in open(path, encoding="utf-8").read().splitlines() if not l.startswith("#")]


def gate():
    """Статусы ворот 03.08 без чисел: busy-replay клетки против эталона (главный — строки tr1x1 прогона busy-skip on)."""
    ok_all = True
    tmp = f"{A}/tmp-p07/julgate"
    os.makedirs(tmp, exist_ok=True)
    for c in CELLS:
        name = c[0]
        got = f"{AUG_HOME}/b5/{name}-julgate/{GATE_DAY}/{pc.BASE_SET}"
        ref = f"{AUG_HOME}/b5/{GATE_REF[name]}/{GATE_DAY}/{pc.BASE_SET}"
        if not os.path.exists(f"{AUG_HOME}/b5/{name}-julgate/{GATE_DAY}/.done"):
            print(f"{name}: нет прогона")
            ok_all = False
            continue
        lab = pc.label(c)
        if name == "p07m-main":
            rep = f"{tmp}/{name}"
            subprocess.run(["python3", f"{A}/bin/busy-replay.py", got, rep], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            a = body(f"{rep}/rounds.csv")
            b = [l for l in body(f"{ref}/rounds.csv") if l.startswith("symbol,") or f",{lab}," in l]
            what = "rounds(busy-replay против busy-skip on)"
        else:
            a, b = body(f"{got}/rounds.csv"), body(f"{ref}/rounds.csv")
            if body(f"{got}/signals.csv") != body(f"{ref}/signals.csv"):
                a = None
            what = "rounds+signals"
        ok = a is not None and a == b
        ok_all &= ok
        print(f"{name}: {what} {'ok' if ok else 'РАЗНИЦА'}")
    print("ВОРОТА 03.08:", "ok" if ok_all else "НЕ ПРОЙДЕНЫ")
    return 0 if ok_all else 1


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--status"
    if mode == "--gate":
        sys.exit(gate())
    n = 0
    for bname, home, day, cells in jobs():
        if queued(bname, day):
            continue
        n += 1
        if mode == "--submit":
            pc.SIGMA_FROM = SIGMA[bname]
            jid = pc.submit(bname, home, day, cells, MEM_GB)
            open(f"{pc.CELLS_DIR}/{bname}-{day}.queued", "w").write(jid + "\n")
            print(f"{bname} {day}: {len(cells)} клеток -> {jid}")
        else:
            print(f"{bname} {day}: {len(cells)} клеток")
    print(f"ИТОГО: {n} суток к постановке")


if __name__ == "__main__":
    main()
