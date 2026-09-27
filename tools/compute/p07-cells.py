#!/usr/bin/env python3
"""П-07 ступень 3 остаток (В-135, CEO 27.09 18:36 -> 18:51 STOP до оптимизаций Инженера):
один вызов --cells на сутки через общую очередь alpha-gridq (не свой юнит, не свой systemd-run
--scope — гридку сам держит ядра/память/резерв, `bin/q-add.sh --tag p07a-cells`).

Компонует на сутки: H6 новые (`at`, `behind`), H13 = П-04 (12 форм выхода `gone*wall*`), H8
(дедлайны 3600/7200) — все на базовом наборе `t-bid-btc4h-q1`; H4 (4 набора `age=`), H5 (3
набора `btc*h_max=`), H3 недостающее (`h3-50000`, `h3-100000`, свои `usd_min=`). Уже готовое
(`.done` под старым поклеточным планировщиком `p07-sched.py`) не пересчитывается.

`--round-memo off` обязателен (не убирать без отдельной проверки Инженера): гейт 27.09
(`tmp-t38/cells-gate.sh`, 23 клетки/11 наборов одним вызовом) нашёл расхождение именно у
`h3-25000` — с `--round-memo on` (умолчание) лишние круги, не совпадающие с поклеточной базой
(старый планировщик всегда считал по одному набору за раз, `--round-memo` там не включался,
`sets.len() > 1` — условие в `bounce_grid.rs`). Проверено здесь эмпирически 27.09: тот же вызов
с `--round-memo off` даёт `rounds.csv`/`forms.csv` `h3-25000` побайтно как база
(`b5/p07a-h3-25000/2026-08-03/t-bid-btc4h-q1/`). Причина — вероятно в разделяемой по формам
памяти кругов (`RoundMemo`, `by_t0`), которая может отдать круг одного набора (например базового
`t-bid-btc4h-q1` без `usd_min`) в набор со своим порогом (`usd_min=25000`), где этот сигнал не
должен быть отобран. Owner/CEO 27.09 18:51: остановить прогоны до починки Инженером; этот файл —
только рабочий инструмент, ждёт сигнала «можно».

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
    python3 p07-cells.py --submit --mem-normal-gb 2.5 --mem-big-gb 6   # переопределить резерв (CEO 27.09: гейт дал пик 1,9 ГБ на 03.08 — 2500/6144 МБ достаточно с запасом, не 6144 всегда)
"""
import os
import subprocess
import sys

A = os.path.expanduser("~/alpha")
BIN = "bin/alpha-cda9acd"
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
          "--entry-ttl-secs 1800 --band-exit-bps 20 --busy-skip off --threads 1 --round-memo off")

BASE_SPEC = "age=2700,side=bid,btc4h_max=-44.55"
BASE_SET = "t-bid-btc4h-q1"

# П-04 на Г-85а (П-07 §13 поправка 2): 3 режима W50/B0 + для каждого 3 соседа W90/B0, W50/B10, W30/B0.
EXITS = [
    "gone50wall0", "gone50wallx0", "gone50wallk0",
    "gone90wall0", "gone90wallx0", "gone90wallk0",
    "gone50wall10", "gone50wallx10", "gone50wallk10",
    "gone30wall0", "gone30wallx0", "gone30wallk0",
]


def static_cells():
    """(out_name, form_label, call_set_name, call_set_spec, canonical_subdir) — одни и те же для
    всех 54 суток (H6 новые, H13=П-04, H8, H4, H5); H3 недостающее — h3_cells(), по суткам."""
    c = []
    c.append(("h6-at", "at-tr1x1-14400-ttl1800", BASE_SET, BASE_SPEC, BASE_SET))
    c.append(("h6-behind", "behind-tr1x1-14400-ttl1800", BASE_SET, BASE_SPEC, BASE_SET))
    for e in EXITS:
        c.append((f"h13-{e}", f"pct2-tr1x1-14400-ttl1800-{e}", BASE_SET, BASE_SPEC, BASE_SET))
    c.append(("h8-3600", "pct2-tr1x1-3600-ttl1800", BASE_SET, BASE_SPEC, BASE_SET))
    c.append(("h8-7200", "pct2-tr1x1-7200-ttl1800", BASE_SET, BASE_SPEC, BASE_SET))
    for v in (900, 1800, 3600, 5400):
        c.append((f"h4-{v}", "pct2-tr1x1-14400-ttl1800", f"h4-{v}",
                   f"age={v},side=bid,btc4h_max=-44.55", BASE_SET))
    c.append(("h5-btc1h", "pct2-tr1x1-14400-ttl1800", "t-bid-btc1h-q1",
              "age=2700,side=bid,btc1h_max=-21.17", "t-bid-btc1h-q1"))
    c.append(("h5-btc2h", "pct2-tr1x1-14400-ttl1800", "t-bid-btc2h-q1",
              "age=2700,side=bid,btc2h_max=-30.56", "t-bid-btc2h-q1"))
    c.append(("h5-btc3h", "pct2-tr1x1-14400-ttl1800", "t-bid-btc3h-q1",
              "age=2700,side=bid,btc3h_max=-38.75", "t-bid-btc3h-q1"))
    return c


def h3_cells():
    return [
        ("h3-50000", "pct2-tr1x1-14400-ttl1800", "h3-50000",
         "age=2700,side=bid,btc4h_max=-44.55,usd_min=50000", BASE_SET),
        ("h3-100000", "pct2-tr1x1-14400-ttl1800", "h3-100000",
         "age=2700,side=bid,btc4h_max=-44.55,usd_min=100000", BASE_SET),
    ]


def day_done(home, out_name, day):
    return os.path.exists(f"{home}/b5/p07a-{out_name}/{day}/.done")


def remaining_for_day(home, day):
    cells = [c for c in static_cells() if not day_done(home, c[0], day)]
    cells += [c for c in h3_cells() if not day_done(home, c[0], day)]
    return cells


def build_job(bname, home, day, cells):
    os.makedirs(CELLS_DIR, exist_ok=True)
    stops = sorted({"pct2", "at", "behind"})
    exits_present = sorted({c[1].split("-ttl1800-", 1)[1] for c in cells if "-ttl1800-" in c[1]})
    sets_needed = {(c[2], c[3]) for c in cells}

    cells_path = f"{CELLS_DIR}/{bname}-{day}.txt"
    with open(cells_path, "w", newline="\n") as f:
        for c in cells:
            f.write(f"{c[1]} {c[2]}\n")

    axes = ["--entry-form single@fr"]
    for s in stops:
        axes.append(f"--stop-form {s}")
    axes.append("--take-form tr1x1")
    for d in (3600, 7200, 14400):
        axes.append(f"--deadline-secs {d}")
    axes.append("--exit-form none")
    for e in exits_present:
        axes.append(f"--exit-form {e}")
    for name, spec in sorted(sets_needed):
        axes.append(f"--set {name}:{spec}")
    axes_str = " ".join(axes)

    out_rel = f"b5/.cellstmp-{day}"
    split_lines = []
    for out_name, label, set_name, _spec, canon in cells:
        dest = f"b5/p07a-{out_name}/{day}"
        split_lines.append(f'mkdir -p "{dest}/{canon}"')
        for f in ("rounds.csv", "forms.csv", "signals.csv"):
            split_lines.append(
                f'awk -F, -v f="{label}" \'NR==1 || $3==f\' {out_rel}/{set_name}/{f} '
                f'> "{dest}/{canon}/{f}"'
            )
        split_lines.append(f'touch "{dest}/.done"')
    split_script = "\n".join(split_lines)

    script = f"""set -e
rm -rf {out_rel}
{BIN} lob bounce-grid --root study/root-{day} --touches-from study/approaches/D20 {COMMON} {axes_str} \\
  --cells {cells_path} --out-dir {out_rel} > {out_rel}.log 2>&1
{split_script}
rm -rf {out_rel}
"""
    return script


def submit(bname, home, day, cells, mem_gb):
    script = build_job(bname, home, day, cells)
    cmd = ["bin/q-add.sh", "--tag", "p07a-cells", "--mem-gb", str(mem_gb),
           "--home", home, "--log", f"tmp-p07/cells-by-day/{bname}-{day}.log",
           "--", "bash", "-c", script]
    out = subprocess.run(cmd, cwd=A, capture_output=True, text=True)
    return out.stdout.strip() or out.stderr.strip()


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "--status"
    only_day = None
    mem_normal = MEM_NORMAL_GB
    mem_big = MEM_BIG_GB
    args = sys.argv[1:]
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
            cells = remaining_for_day(home, day)
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
