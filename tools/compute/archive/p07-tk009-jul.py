#!/usr/bin/env python3
"""TK-009, отбор на июль по В-146 (владелец через CEO 09:39; Судья `6960d7d`): клетки пачки с итогом $ ≥ 0 и в
августе, и в сентябре — на июль одним заданием на сутки через `alpha-gridq` (В-136).

Клетки (34, различных 33 — h2-0.0136 ≡ h2-0.0273 после K ≤ 1): 7 approach-клеток сетки TK-009 (`p07-tk009-cells.py`:
market, ttl60, ttl300, gone50be, gone90be, gone50tr1, gone90tr1) + 24 прежние оси Г-85б (`p07-cells.py`: H2 ×3, H6
pct1.5/pct3, H13 gone50wallk0/gone90wall0/gone90wallk0/gone50wallk10, H8 ×2, H4 ×4, H5 ×3, H3 25000/50000, H7 ×4) —
ОДИН `bounce-grid --cells` (оси — объединение, наборы — свои у H3/H4/H5); Г-147 touch — второй проход того же задания
(сигнал не ось сетки, как в авг/сен). K≤1×H1 f25 и K≤1×H10 cap5 — пост-обработка `p07b-base` июля (уже посчитан, TK-010).
Бинарник, флаги, σ₂₄₀ июля (`epochs/e-jul/study/sigma240`), кэш подходов — как `p07-jul-cells.py`.

Не слито с июльскими заданиями П-08: там свой бинарник TK-012 с `--p08-cols` и свежий `lob touches`, а здесь 9 наборов
и 31 клетка — задание П-08 (TK-013) менять ради ~1 прохода на сутки не стал; выигрыш ≤ ~10 мин стены.

Ворота (как поправка 5 п. 2): те же клетки тем же заданием на 03.08 в `epochs/e-aug/b5/<клетка>-julgate` — сверка
rounds+signals (без строк `#`) с уже посчитанными на авг; печать — только ok/РАЗНИЦА.

    python3 bin/p07-tk009-jul.py --status | --submit [--only-day D] | --gate
"""
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


pc = load("p07cells", "p07-cells.py")
T9 = load("t9", "p07-tk009-cells.py")
A = pc.A
JUL_HOME = f"{A}/epochs/e-jul"
AUG_HOME = f"{A}/epochs/e-aug"
JUL_DAYS = [f"2026-07-{d:02d}" for d in range(1, 32)]
GATE_DAY = "2026-08-03"
SIGMA = {"jul": f"{JUL_HOME}/study/sigma240", "julgate": f"{A}/study/sigma240"}
MEM_GB = 3.0

OLD = {"p07b-h2-0.0136", "p07b-h2-0.0273", "p07b-h2-0.0682", "p07b-h6-pct1.5", "p07b-h6-pct3",
       "p07b-h13-gone50wallk0", "p07b-h13-gone90wall0", "p07b-h13-gone90wallk0", "p07b-h13-gone50wallk10",
       "p07b-h8-3600", "p07b-h8-7200", "p07b-h4-900", "p07b-h4-1800", "p07b-h4-3600", "p07b-h4-5400",
       "p07b-h5-btc1h", "p07b-h5-btc2h", "p07b-h5-btc3h", "p07b-h3-25000", "p07b-h3-50000",
       "p07b-h7-tr1.5x1", "p07b-h7-tr1x1.5", "p07b-h7-tr2x1", "p07b-h7-1to1"}
NEW = {"p07b-t9-market", "p07b-t9-ttl60", "p07b-t9-ttl300", "p07b-t9-gone50be", "p07b-t9-gone90be",
       "p07b-t9-gone50tr1", "p07b-t9-gone90tr1", "p07b-t9-touch"}
OLD_CELLS = [c for c in pc.all_cells() if c[0] in OLD]
NEW_APPR = [c for c in T9.CELLS if c[0] in NEW and c[2] == "approach"]
NEW_TOUCH = [c for c in T9.CELLS if c[0] in NEW and c[2] == "touch"]
assert len(OLD_CELLS) == 24 and len(NEW_APPR) == 7 and len(NEW_TOUCH) == 1
# оси сетки TK-009, которых нет у прежних клеток (early-exit не нужен: early1/2/3 не прошли В-146)
T9_AXES = ("--entry-form market --entry-ttl-secs 60 --entry-ttl-secs 300 "
           "--exit-form gone50be --exit-form gone90be --exit-form gone50tr1 --exit-form gone90tr1")


def suffix(bname):
    return "-julgate" if bname == "julgate" else ""


def cells_for(bname, home, day):
    s = suffix(bname)
    old = [(c[0] + s,) + c[1:] for c in OLD_CELLS if not pc.day_done(home, c[0] + s, day)]
    appr = [(c[0] + s,) + c[1:] for c in NEW_APPR if not pc.day_done(home, c[0] + s, day)]
    touch = [(c[0] + s,) + c[1:] for c in NEW_TOUCH if not pc.day_done(home, c[0] + s, day)]
    return old, appr, touch


def build_job(bname, home, day, old, appr, touch):
    pc.SIGMA_FROM = T9.P.SIGMA_FROM = SIGMA[bname]
    if not old:  # прежние готовы — клетки сетки обычным путём TK-009
        return T9.build_job(f"j9-{bname}", day, appr + touch)
    # прежние клетки дают оси/наборы/раскладку (`p07-cells.build_job`); клетки сетки дописываются в тот же --cells
    script = pc.build_job(f"j9-{bname}", home, day, old)
    cells_path = f"{pc.CELLS_DIR}/j9-{bname}-{day}.txt"
    with open(cells_path, "a", newline="\n") as f:
        for _o, label, _s in appr:
            f.write(f"{label} {pc.BASE_SET}\n")
    if appr:
        assert f" --cells {cells_path}" in script
        script = script.replace(f" --cells {cells_path}", f" {T9_AXES} --cells {cells_path}", 1)
        if f"--set {pc.BASE_SET}:" not in script:
            script = script.replace(f" --cells {cells_path}", f" --set {pc.BASE_SET}:{pc.BASE_SPEC} --cells {cells_path}", 1)
    out_rel = f"b5/.cellstmp-{day}"
    split = []
    for out_name, label, _s in appr:
        dest = f"b5/{out_name}/{day}"
        split.append(f'mkdir -p "{dest}/{pc.BASE_SET}"')
        for fn in ("rounds.csv", "forms.csv", "signals.csv"):
            split.append(f'awk -F, -v f="{label}" \'NR<=2 || $3==f\' {out_rel}/{pc.BASE_SET}/{fn} '
                         f'> "{dest}/{pc.BASE_SET}/{fn}"')
        split.append(f'[ "$(wc -l < "{dest}/{pc.BASE_SET}/forms.csv")" -ge 3 ] || '
                     f'{{ echo "нет формы {label}" >&2; exit 3; }}')
        split.append(f'touch "{dest}/.done"')
    tail = f"rm -rf {out_rel}\n"
    assert script.endswith(tail)
    script = script[: -len(tail)] + "\n".join(split) + "\n" + tail
    if touch:
        script += T9.build_job(f"j9-{bname}", day, touch)
    return script


def jobs():
    out = []
    old, appr, touch = cells_for("julgate", AUG_HOME, GATE_DAY)
    if old or appr or touch:
        out.append(("julgate", AUG_HOME, GATE_DAY, old, appr, touch))
    for day in JUL_DAYS:
        if not os.path.exists(f"{JUL_HOME}/study/approaches/D20/{day}/.done"):
            continue
        old, appr, touch = cells_for("jul", JUL_HOME, day)
        if old or appr or touch:
            out.append(("jul", JUL_HOME, day, old, appr, touch))
    return out


def body(path):
    return [l for l in open(path, encoding="utf-8").read().splitlines() if not l.startswith("#")]


def gate():
    ok_all = True
    for c in OLD_CELLS + NEW_APPR + NEW_TOUCH:
        name, canon = c[0], (c[8] if len(c) > 3 else pc.BASE_SET)
        got = f"{AUG_HOME}/b5/{name}-julgate/{GATE_DAY}/{canon}"
        ref = f"{AUG_HOME}/b5/{name}/{GATE_DAY}/{canon}"
        if not os.path.exists(f"{AUG_HOME}/b5/{name}-julgate/{GATE_DAY}/.done"):
            print(f"{name}: нет прогона")
            ok_all = False
            continue
        ok = all(body(f"{got}/{f}") == body(f"{ref}/{f}") for f in ("rounds.csv", "signals.csv"))
        ok_all &= ok
        print(f"{name}: {'ok' if ok else 'РАЗНИЦА'}")
    print("ВОРОТА 03.08:", "ok" if ok_all else "НЕ ПРОЙДЕНЫ")
    return 0 if ok_all else 1


def main():
    args = sys.argv[1:]
    if "--gate" in args:
        sys.exit(gate())
    mode = "--submit" if "--submit" in args else "--status"
    only = args[args.index("--only-day") + 1] if "--only-day" in args else None
    n = 0
    for bname, home, day, old, appr, touch in jobs():
        if only and day != only:
            continue
        mark = f"{pc.CELLS_DIR}/j9-{bname}-{day}.queued"
        if os.path.exists(mark):
            continue
        n += 1
        k = len(old) + len(appr) + len(touch)
        if mode != "--submit":
            print(f"{bname} {day}: {k} клеток")
            continue
        script = build_job(bname, home, day, old, appr, touch)
        cmd = ["bin/q-add.sh", "--tag", "p07-j9", "--mem-gb", str(MEM_GB), "--home", home,
               "--log", f"tmp-p07/cells-by-day/j9-{bname}-{day}.log", "--", "bash", "-c", script]
        r = pc.subprocess.run(cmd, cwd=A, capture_output=True, text=True)
        jid = (r.stdout or r.stderr).strip()
        open(mark, "w").write(jid + "\n")
        print(f"{bname} {day}: {k} клеток -> {jid}")
    print(f"ИТОГО: {n} суток")


if __name__ == "__main__":
    main()
