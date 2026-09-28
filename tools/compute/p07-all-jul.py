#!/usr/bin/env python3
"""TK-018 (владелец 28.09 ~11:40 «а че в июле вообще нихуя нету. дорабатывай!»): июль для ВСЕХ вариантов дашборда —
один `bounce-grid --cells` на сутки (В-136) через `alpha-gridq`, все клетки сразу; неначатые сутки p07-j9 (TK-009)
слиты сюда (`--cancel-j9` снимает их из очереди, клетки j9 входят в это задание).

Клетки сетки (подход, база В-65, флаги/бинарник — `p07-cells.py` без правок):
  * Г-85а — все оси П-07 (`p07-cells.all_cells` + посчитанные раньше поклеточно: база, H2 fr+1..3, H6 pct1.5/pct3/before,
    H7 ×4, H3 25000);
  * Г-85б — все оси П-07 (`all_cells`) + H2 ×3 + H7 ×4;
  * пачка TK-009 — все 12 клеток `p07-tk009-cells.py` (early1/2/3 тоже — владелец «все»), touch — второй проход;
  * главный и его соседи на форме В-104 `ladder3x2..20w2-pct2-tr1x1-14400`: наборы П-05 (153, спецы — из
    `epochs/e-aug/b5/p05-<часть>/2026-08-03/manifest.txt`), MAIN_EXTRA (take / без фильтра / кандидат), П-02 Г-105/Г-86.
Пост-обработки (H9 пауза/потолок, H9р, H14, K ≤ 1, H10 cap, Г-08) сетки не требуют — при чтении из этих клеток.
П-08 — своя сетка TK-013 (бинарник TK-012 `--p08-cols`), здесь нет.

Ворота (поправка 5 п. 2): все клетки тем же заданием на 03.08 в `epochs/e-aug/b5/<каталог>-julgate`; сверка с
посчитанным на авг: rounds (+signals) без строк `#` побайтно; эталон, считанный с busy-skip on, — через busy-replay.
Печать — только ok/РАЗНИЦА.

    python3 bin/p07-all-jul.py --status | --submit [--only-day D] [--cancel-j9] | --gate
"""
import importlib.util
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


j9 = load("j9", "p07-tk009-jul.py")
pc, T9 = j9.pc, j9.T9
A, JUL_HOME, AUG_HOME, GATE_DAY = pc.A, j9.JUL_HOME, j9.AUG_HOME, j9.GATE_DAY
MEM_GB = 4.0
TAG = "p07-jall"
MAIN_ENTRY = "ladder3x2..20w2"
Q = f"{A}/queue"

# --- клетки -------------------------------------------------------------------------------------------------------
A_EXTRA = [pc.cell("p07a-base", pc.ENTRY_A)]
A_EXTRA += [pc.cell(f"p07a-h2-fr{k}", f"single@fr+{k}") for k in (1, 2, 3)]
A_EXTRA += [pc.cell(f"p07a-h6-{s}", pc.ENTRY_A, stop=s) for s in ("pct1.5", "pct3", "before")]
A_EXTRA += [pc.cell(f"p07a-h7-{t}", pc.ENTRY_A, take=t) for t in ("tr1.5x1", "tr1x1.5", "tr2x1", "1to1")]
A_EXTRA += [pc.cell("p07a-h3-25000", pc.ENTRY_A, set_name="h3-25000", spec=f"{pc.BASE_SPEC},usd_min=25000")]

# главный (TK-010 `p07m-main`) и соседи; (клетка, эталон 03.08 — каталог, подкаталог набора)
MAIN_EXTRA = [
    (pc.cell("p07m-main", MAIN_ENTRY), (f"{AUG_HOME}/b5/titrc-u500r", pc.BASE_SET)),
    # `titration-dashboard-merge.py:31-33`: btc4h_take — тейк 1,75 %; cand — BTC 1 ч; nofilter — набор t-bid-age-45
    (pc.cell("p07m-take", MAIN_ENTRY, take="tk1.75"), (f"{AUG_HOME}/b5/titrc-u500r", pc.BASE_SET)),
    (pc.cell("p07m-btc1h", MAIN_ENTRY, set_name="t-bid-btc1h-q1", spec="age=2700,side=bid,btc1h_max=-21.17",
             canon="t-bid-btc1h-q1"), (f"{AUG_HOME}/b5/titrc-u500r", "t-bid-btc1h-q1")),
    (pc.cell("p07m-nofilter", MAIN_ENTRY, set_name="t-bid-age-45", spec="age=2700,side=bid", canon="t-bid-age-45"),
     (f"{AUG_HOME}/b5/titrc-u500r", "t-bid-age-45")),
    # П-02 Г-105: стоп «до стены» (`b5/p02-h10-before`)
    (pc.cell("p02-h10-before", MAIN_ENTRY, stop="before"), (f"{AUG_HOME}/b5/p02-h10-before", pc.BASE_SET)),
    # П-02 Г-86: вход по рынку после съедания ≥ 89,9 % (`~/alpha/b5/p02-h9e899-aug-market`, набор под именем базы)
    (pc.cell("p02-h9e899-market", "market", set_name="g86-e899", spec=f"{pc.BASE_SPEC},eaten_min=89.9"),
     (f"{A}/b5/p02-h9e899-aug-market", pc.BASE_SET)),
]

P05_PARTS = ("a1", "a2", "b", "c")


def p05_cells():
    out = []
    for part in P05_PARTS:
        man = f"{AUG_HOME}/b5/p05-{part}/{GATE_DAY}/manifest.txt"
        for line in open(man, encoding="utf-8"):
            if not line.startswith("set="):
                continue
            name, spec = line.strip()[4:].split(":", 1)
            c = pc.cell(f"p05-{part}", MAIN_ENTRY, set_name=name, spec=spec, canon=name)
            out.append((c, (f"{AUG_HOME}/b5/p05-{part}", name)))
    return out


def grid_cells():
    """[(клетка p07-cells, (эталон-каталог 03.08, подкаталог))] — всё, что идёт одним --cells (кроме сетки TK-009)."""
    out = []
    for c in pc.all_cells() + A_EXTRA:
        out.append((c, (f"{AUG_HOME}/b5/{c[0]}", c[8])))
    out += MAIN_EXTRA + p05_cells()
    names = [(c[0], c[8]) for c, _ in out]
    assert len(names) == len(set(names)), "повтор клетки"
    return out


T9_APPR = [c for c in T9.CELLS if c[2] == "approach"]
T9_TOUCH = [c for c in T9.CELLS if c[2] == "touch"]
T9_AXES = ("--entry-form market --entry-ttl-secs 60 --entry-ttl-secs 300 "
           "--exit-form gone50be --exit-form gone90be --exit-form gone50tr1 --exit-form gone90tr1 "
           "--early-exit-secs off --early-exit-secs 1 --early-exit-secs 2 --early-exit-secs 3")


def cell_done(home, c, day):
    return os.path.exists(f"{home}/b5/{c[0]}/{day}/{c[8]}/rounds.csv") and pc.day_done(home, c[0], day)


def j9_owned(bname, day):
    """Сутки, чьё задание p07-j9 уже идёт/прошло (метка .queued не снята) — клетки j9 считает оно, не мы."""
    return os.path.exists(f"{pc.CELLS_DIR}/j9-{bname}-{day}.queued")


def cells_for(bname, home, day):
    s = j9.suffix(bname)
    skip = (j9.OLD | j9.NEW) if j9_owned(bname, day) else set()
    old = [(c[0] + s,) + c[1:] for c, _ in grid_cells()
           if c[0] not in skip and not cell_done(home, (c[0] + s,) + c[1:], day)]
    appr = [(c[0] + s,) + c[1:] for c in T9_APPR if c[0] not in skip and not pc.day_done(home, c[0] + s, day)]
    touch = [(c[0] + s,) + c[1:] for c in T9_TOUCH if c[0] not in skip and not pc.day_done(home, c[0] + s, day)]
    return old, appr, touch


def build_job(bname, home, day, old, appr, touch):
    """Как `p07-tk009-jul.build_job` (своё имя файлов `jall-*`), ось early для early1/2/3."""
    pre = f"jall-{bname}"
    if not old:
        return T9.build_job(pre, day, appr + touch)
    script = pc.build_job(pre, home, day, old)
    cells_path = f"{pc.CELLS_DIR}/{pre}-{day}.txt"
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
        script += T9.build_job(pre, day, touch)
    return script


def jobs(only=None):
    out = []
    if only in (None, GATE_DAY):
        old, appr, touch = cells_for("julgate", AUG_HOME, GATE_DAY)
        if old or appr or touch:
            out.append(("julgate", AUG_HOME, GATE_DAY, old, appr, touch))
    for day in j9.JUL_DAYS:
        if only and day != only:
            continue
        if not os.path.exists(f"{JUL_HOME}/study/approaches/D20/{day}/.done"):
            continue
        old, appr, touch = cells_for("jul", JUL_HOME, day)
        if old or appr or touch:
            out.append(("jul", JUL_HOME, day, old, appr, touch))
    return out


def cancel_j9():
    """Неначатые задания p07-j9 → queue/cancelled-tk018 (+ снять их метки .queued, чтобы сутки ушли сюда)."""
    dst = f"{Q}/cancelled-tk018"
    os.makedirs(dst, exist_ok=True)
    n = 0
    for fn in sorted(os.listdir(f"{Q}/pending")):
        if "-p07-j9-" not in fn:
            continue
        log = [l for l in open(f"{Q}/pending/{fn}", encoding="utf-8") if l.startswith("JOB_LOG=")][0]
        tag = os.path.basename(log.strip().split("=", 1)[1])[:-4]  # j9-jul-2026-07-DD
        try:
            os.rename(f"{Q}/pending/{fn}", f"{dst}/{fn}")
        except FileNotFoundError:
            continue  # демон успел взять
        mark = f"{pc.CELLS_DIR}/{tag}.queued"
        if os.path.exists(mark):
            os.rename(mark, mark + ".cancelled-tk018")
        n += 1
    print(f"снято p07-j9: {n}")


def body(path):
    return [l for l in open(path, encoding="utf-8").read().splitlines() if not l.startswith("#")]


def gate():
    ok_all = True
    tmp = f"{A}/tmp-p07/jallgate"
    os.makedirs(tmp, exist_ok=True)
    refs = [(c, r) for c, r in grid_cells()]
    refs += [(c, (f"{AUG_HOME}/b5/{c[0]}", pc.BASE_SET)) for c in T9_APPR + T9_TOUCH]
    for c, (ref_dir, sub) in refs:
        name = c[0]
        got = f"{AUG_HOME}/b5/{name}-julgate/{GATE_DAY}/{sub}"
        ref = f"{ref_dir}/{GATE_DAY}/{sub}"
        key = f"{name}/{sub}"
        if not os.path.exists(f"{got}/rounds.csv"):
            print(f"{key}: нет прогона")
            ok_all = False
            continue
        if not os.path.exists(f"{ref}/rounds.csv"):
            print(f"{key}: нет эталона")
            ok_all = False
            continue
        lab = pc.label(c) if len(c) > 3 else c[1]
        b = [l for l in body(f"{ref}/rounds.csv") if l.startswith("symbol,") or f",{lab}," in l]
        a = body(f"{got}/rounds.csv")
        mode = "rounds+signals"
        ok = a == b and body(f"{got}/signals.csv") == [
            l for l in body(f"{ref}/signals.csv") if l.startswith("symbol,") or f",{lab}," in l]
        if not ok:  # эталон с busy-skip on — сверка через busy-replay
            rep = f"{tmp}/{name}-{sub}"
            subprocess.run(["python3", f"{A}/bin/busy-replay.py", got, rep], check=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            ok, mode = body(f"{rep}/rounds.csv") == b, "rounds(busy-replay)"
        ok_all &= ok
        print(f"{key}: {mode} {'ok' if ok else 'РАЗНИЦА'}")
    print("ВОРОТА 03.08:", "ok" if ok_all else "НЕ ПРОЙДЕНЫ")
    return 0 if ok_all else 1


def main():
    args = sys.argv[1:]
    if "--gate" in args:
        sys.exit(gate())
    mode = "--submit" if "--submit" in args else "--status"
    only = args[args.index("--only-day") + 1] if "--only-day" in args else None
    if mode == "--submit" and "--cancel-j9" in args:
        cancel_j9()
    n = total = 0
    for bname, home, day, old, appr, touch in jobs(only):
        mark = f"{pc.CELLS_DIR}/jall-{bname}-{day}.queued"
        if os.path.exists(mark):
            continue
        n += 1
        k = len(old) + len(appr) + len(touch)
        total += k
        if mode != "--submit":
            print(f"{bname} {day}: {k} клеток")
            continue
        pc.SIGMA_FROM = T9.P.SIGMA_FROM = j9.SIGMA[bname]
        script = build_job(bname, home, day, old, appr, touch)
        prio = "4" if bname == "julgate" else "5"
        # сценарий ~180 КБ (153 набора П-05) — больше предела одного аргумента execve (128 КБ): файлом, не `bash -c`
        sh = f"{pc.CELLS_DIR}/jall-{bname}-{day}.sh"
        with open(sh, "w", newline="\n") as f:
            f.write(script)
        cmd = ["bin/q-add.sh", "--tag", TAG, "--prio", prio, "--mem-gb", str(MEM_GB), "--home", home,
               "--log", f"tmp-p07/cells-by-day/jall-{bname}-{day}.log", "--", "bash", sh]
        r = subprocess.run(cmd, cwd=A, capture_output=True, text=True)
        jid = (r.stdout or r.stderr).strip()
        open(mark, "w").write(jid + "\n")
        print(f"{bname} {day}: {k} клеток -> {jid}")
    print(f"ИТОГО: {n} суток, {total} клетка-суток")


if __name__ == "__main__":
    main()
