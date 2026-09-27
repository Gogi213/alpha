#!/usr/bin/env python3
"""П-07 ступень 3 остаток (В-135, CEO 27.09 18:36 -> 18:51 STOP до оптимизаций Инженера):
один вызов --cells на сутки через общую очередь alpha-gridq (не свой юнит, не свой systemd-run
--scope — гридку сам держит ядра/память/резерв, `bin/q-add.sh --tag p07a-cells`).

Компонует на сутки: H6 новые (`at`, `behind`), H13 = П-04 (12 форм выхода `gone*wall*`), H8
(дедлайны 3600/7200) — все на базовом наборе `t-bid-btc4h-q1`; H4 (4 набора `age=`), H5 (3
набора `btc*h_max=`), H3 недостающее (`h3-50000`, `h3-100000`, свои `usd_min=`). Уже готовое
(`.done` под старым поклеточным планировщиком `p07-sched.py`) не пересчитывается.

`--round-memo on` (умолчание; Инженер 27.09, `3870ff6`): прежнее «расхождение `h3-25000`» в гейте
`tmp-t38/cells-gate.sh` было путаницей имён — старые поодиночные прогоны хранят набор `usd_min=25000` под
именем `t-bid-btc4h-q1`; `memorepro.sh` и 23 клетки 03.08 с memo on = memo off побайтно; ключ memo дополнен
сиротами на входе. Бинарник и флаги (`--hold-step skip`) — от Инженера после его гейта.

Раскладка после счёта — в этом же вызове q-add (одна задача = compute + split, не два задания:
gridq не гарантирует порядок между разными заданиями одной серии, только между стартом и
завершением ОДНОГО). `awk -F, '$3==<форма>'` режет по колонке `form` — она одна на
rounds.csv/forms.csv/signals.csv, схема одна. Наборы `h3-*`/`h4-*` (свой `--set` с чужим usd_min/
age в имени `t-bid-btc4h-q1` было бы неуникально) переименовываются в канонический
`t-bid-btc4h-q1` при раскладке — так же, как уже сложилось у `h3-25000` старого планировщика
(проверено: там подпапка внутри `p07a-h3-25000/<день>/` называется `t-bid-btc4h-q1`, не
`h3-25000`, хотя набор для CLI назывался `h3-25000`). `h5-*`/`t-bid-btc4h-q1` сами по себе —
канонические имена, без переименования.

    python3 p07-cells.py --status                         # сколько клетка-суток осталось
    python3 p07-cells.py --submit                          # положить весь остаток в gridq (сигнал владельца/CEO нужен)
    python3 p07-cells.py --submit --only-day 2026-08-03    # одни сутки (проверка)
    python3 p07-cells.py --submit --only-day 2026-08-03 --check-base   # проба: + база Г-85а в p07a-base-recheck (сверка побайтно)
    python3 p07-cells.py --submit --mem-normal-gb 2.5 --mem-big-gb 6   # переопределить резерв (CEO 27.09: гейт дал пик 1,9 ГБ на 03.08 — 2500/6144 МБ достаточно с запасом, не 6144 всегда)
"""
import os
import subprocess
import sys

A = os.path.expanduser("~/alpha")
BIN = "bin/alpha-e74f200-v3"  # T-38 принят Судьёй `2b93ed7` (TK-004)
CELLS_DIR = f"{A}/tmp-p07/cells-by-day"

HOMES = [
    ("aug", f"{A}/epochs/e-aug", [f"2026-08-{d:02d}" for d in range(1, 32)]),
    ("sep1", f"{A}/tmp-lsk0914.used-20260926/home", [f"2026-09-{d:02d}" for d in range(1, 16)]),
    ("sep2", f"{A}/tmp-t29/rec", [f"2026-09-{d:02d}" for d in range(16, 24)]),
]

# Те же крупные сутки, что в p07-sched.py (binlog bytes > 3.5e9, замер 27.09) — гейт «--cells»
# Инженера на 03.08 (обычные сутки) дал пик 1,9 ГБ; CEO 27.09 18:51: резерв ~2500 МБ обычным,
# 6144 МБ крупным (не 6144 всем — тройной запас пустовал).
BIG_DAYS = {
    ("sep1", "2026-09-14"), ("sep1", "2026-09-15"),
    ("sep2", "2026-09-16"), ("sep2", "2026-09-17"), ("sep2", "2026-09-18"), ("sep2", "2026-09-19"),
    ("sep2", "2026-09-20"), ("sep2", "2026-09-21"), ("sep2", "2026-09-22"), ("sep2", "2026-09-23"),
}
MEM_NORMAL_GB = 2.5
MEM_BIG_GB = 6.0

RTT = ("--median-rtt-ns place=4200000,cancel=3980000,taker=5650000 "
       "--p95-rtt-ns place=4790000,cancel=4550000,taker=6420000")
COMMON = (f"--signal approach --queue-model prob:3 {RTT} --regime-from study/regime "
          "--order-usd 500 --carry-root root --h3-mode notional --h3-usd 10000 "
          "--entry-ttl-secs 1800 --band-exit-bps 20 --busy-skip off --threads 1 "
          "--hold-step skip --exit-group on")

BASE_SPEC = "age=2700,side=bid,btc4h_max=-44.55"
BASE_SET = "t-bid-btc4h-q1"
SIGMA_FROM = f"{A}/study/sigma240"  # В-131: σ₂₄₀ₘᵢₙ на взводе (`sigma-table.py`)

# П-04 на Г-85а/Г-85б (П-07 §13 поправка 2): 3 режима W50/B0 + для каждого 3 соседа W90/B0, W50/B10, W30/B0.
EXITS = [
    "gone50wall0", "gone50wallx0", "gone50wallk0",
    "gone90wall0", "gone90wallx0", "gone90wallk0",
    "gone50wall10", "gone50wallx10", "gone50wallk10",
    "gone30wall0", "gone30wallx0", "gone30wallk0",
]

# Г-85а — `single@fr` (имя формы без поля входа); Г-85б — σ-лестница В-131 (П-07 поправка 4), k = k₀ × {1/3, 2/3, 1, 5/3}.
ENTRY_A = "single@fr"
ENTRY_B = "ladder3x0..0.0409sw2"
ENTRY_B_H2 = ["ladder3x0..0.0136sw2", "ladder3x0..0.0273sw2", "ladder3x0..0.0682sw2"]

H5_SETS = [
    ("btc1h", "t-bid-btc1h-q1", "age=2700,side=bid,btc1h_max=-21.17"),
    ("btc2h", "t-bid-btc2h-q1", "age=2700,side=bid,btc2h_max=-30.56"),
    ("btc3h", "t-bid-btc3h-q1", "age=2700,side=bid,btc3h_max=-38.75"),
]


def cell(outdir, entry, stop="pct2", take="tr1x1", dl=14400, exit_form="none",
         set_name=BASE_SET, spec=BASE_SPEC, canon=BASE_SET):
    """Клетка: (каталог b5/<outdir>, вход, стоп, тейк, дедлайн, выход, набор CLI, спец набора, имя подпапки)."""
    return (outdir, entry, stop, take, dl, exit_form, set_name, spec, canon)


def label(c):
    """Имя формы в rounds/forms/signals.csv — как `grid_forms_with_axes` + `form_label_with_entry`."""
    _o, entry, stop, take, dl, ex, *_ = c
    base = f"{stop}-{take}-{dl}" if entry == ENTRY_A else f"{entry}-{stop}-{take}-{dl}"
    base += "-ttl1800"
    return base if ex == "none" else f"{base}-{ex}"


def axes_1d(prefix, entry, stops):
    """Одномерные оси §13 от базы варианта (H3–H8, H13); H3 у каждого варианта — $25k/$50k/$100k."""
    c = [cell(f"{prefix}-h6-{s}", entry, stop=s) for s in stops]
    c += [cell(f"{prefix}-h13-{e}", entry, exit_form=e) for e in EXITS]
    c += [cell(f"{prefix}-h8-{d}", entry, dl=d) for d in (3600, 7200)]
    c += [cell(f"{prefix}-h4-{v}", entry, set_name=f"h4-{v}",
               spec=f"age={v},side=bid,btc4h_max=-44.55") for v in (900, 1800, 3600, 5400)]
    c += [cell(f"{prefix}-h5-{n}", entry, set_name=s, spec=sp, canon=s) for n, s, sp in H5_SETS]
    c += [cell(f"{prefix}-h3-{v}", entry, set_name=f"h3-{v}",
               spec=f"{BASE_SPEC},usd_min={v}") for v in (25000, 50000, 100000)]
    return c


def all_cells():
    """TK-004: остаток Г-85а (ступень 3 + H6 at/behind + H13) и Г-85б целиком (база + H2–H8, H13).
    Г-85а база, H6 pct1.5/pct3/before, H7, H2, h3-25000 — уже посчитаны (`.done`) и не пересчитываются."""
    a = axes_1d("p07a", ENTRY_A, ["at", "behind"])
    a = [c for c in a if c[0] != "p07a-h3-25000"]
    b = [cell("p07b-base", ENTRY_B)]
    b += [cell(f"p07b-h2-{e.split('..')[1][:-3]}", e) for e in ENTRY_B_H2]
    b += axes_1d("p07b", ENTRY_B, ["pct1.5", "pct3", "before", "at", "behind"])
    b += [cell(f"p07b-h7-{t}", ENTRY_B, take=t) for t in ("tr1.5x1", "tr1x1.5", "tr2x1", "1to1")]
    return a + b


def day_done(home, out_name, day):
    return os.path.exists(f"{home}/b5/{out_name}/{day}/.done")


def remaining_for_day(home, day, check_base=False, redo=False):
    # redo — пересчитать клетки пачки поверх (без удаления `.done`); h3-50000 прежнего планировщика не трогать
    cells = [c for c in all_cells()
             if (redo and c[0] != "p07a-h3-50000") or not day_done(home, c[0], day)]
    if check_base and cells:
        # Сверка метода: база Г-85а уже есть — пересчитать её в этом же вызове в p07a-base-recheck и сравнить побайтно.
        cells.append(cell("p07a-base-recheck", ENTRY_A))
    return cells


def build_job(bname, home, day, cells):
    os.makedirs(CELLS_DIR, exist_ok=True)
    uniq = lambda i: sorted({c[i] for c in cells}, key=str)
    sets_needed = sorted({(c[6], c[7]) for c in cells})

    cells_path = f"{CELLS_DIR}/{bname}-{day}.txt"
    with open(cells_path, "w", newline="\n") as f:
        for c in cells:
            f.write(f"{label(c)} {c[6]}\n")

    axes = [f"--entry-form {e}" for e in uniq(1)]
    axes += [f"--stop-form {s}" for s in uniq(2)]
    axes += [f"--take-form {t}" for t in uniq(3)]
    axes += [f"--deadline-secs {d}" for d in uniq(4)]
    axes += [f"--exit-form {e}" for e in uniq(5)]
    axes += [f"--set {n}:{s}" for n, s in sets_needed]
    if any(e != ENTRY_A for e in uniq(1)):
        axes.append(f"--sigma-from {SIGMA_FROM}")
    axes_str = " ".join(axes)

    out_rel = f"b5/.cellstmp-{day}"
    split_lines = []
    for c in cells:
        out_name, set_name, canon = c[0], c[6], c[8]
        dest = f"b5/{out_name}/{day}"
        split_lines.append(f'mkdir -p "{dest}/{canon}"')
        for f in ("rounds.csv", "forms.csv", "signals.csv"):
            split_lines.append(
                f'awk -F, -v f="{label(c)}" \'NR<=2 || $3==f\' {out_rel}/{set_name}/{f} '
                f'> "{dest}/{canon}/{f}"'
            )
        # строка 1 — «# lob bounce-grid: …», 2 — имена колонок (проба 03.08: NR==1 их теряла)
        # имя формы не совпало — forms.csv без строк: стоп, а не пустая клетка с `.done`
        split_lines.append(f'[ "$(wc -l < "{dest}/{canon}/forms.csv")" -ge 3 ] || '
                           f'{{ echo "нет формы {label(c)} в {set_name}" >&2; exit 3; }}')
        split_lines.append(f'touch "{dest}/.done"')
    split_script = "\n".join(split_lines)

    script = f"""set -e
rm -rf {out_rel}
{BIN} lob bounce-grid --root study/root-{day} --touches-from study/approaches/D20 {COMMON} {axes_str} \
  --cells {cells_path} --out-dir {out_rel} > {out_rel}.log 2>&1
cp {out_rel}.log {CELLS_DIR}/{bname}-{day}.grid.log
{split_script}
rm -rf {out_rel}
"""
    return script


def submit(bname, home, day, cells, mem_gb):
    script = build_job(bname, home, day, cells)
    cmd = ["bin/q-add.sh", "--tag", "p07-cells", "--mem-gb", str(mem_gb),
           "--home", home, "--log", f"tmp-p07/cells-by-day/{bname}-{day}.log",
           "--", "bash", "-c", script]
    out = subprocess.run(cmd, cwd=A, capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


def only_day_given(args):
    return "--only-day" in args  # --redo — только для одних суток


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--status"
    only_day = None
    mem_normal = MEM_NORMAL_GB
    mem_big = MEM_BIG_GB
    args = sys.argv[1:]
    check_base = "--check-base" in args
    redo = "--redo" in args and only_day_given(args)
    for i, a in enumerate(args):
        if a == "--only-day" and i + 1 < len(args):
            only_day = args[i + 1]
        if a == "--mem-normal-gb" and i + 1 < len(args):
            mem_normal = float(args[i + 1])
        if a == "--mem-big-gb" and i + 1 < len(args):
            mem_big = float(args[i + 1])

    total_cells = 0
    total_days = 0
    for bname, home, days in HOMES:
        for day in days:
            if only_day and day != only_day:
                continue
            cells = remaining_for_day(home, day, check_base, redo)
            if not cells:
                continue
            total_days += 1
            total_cells += len(cells)
            if mode == "--submit":
                mem = mem_big if (bname, day) in BIG_DAYS else mem_normal
                jid = submit(bname, home, day, cells, mem)
                print(f"{bname} {day}: {len(cells)} клеток, {mem} ГБ -> {jid}")
    print(f"ИТОГО: {total_days} суток, {total_cells} клетка-суток")


if __name__ == "__main__":
    main()
