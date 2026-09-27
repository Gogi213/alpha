#!/usr/bin/env python3
"""TK-009 (П-07 поправка 6, п. 1): 12 клеток на форме Г-85б, один `--cells` на сутки (В-136), очередь `alpha-gridq`.

Клетки — база Г-85б (`p07-cells.py`: ENTRY_B, pct2, tr1x1, 14400, ttl1800, набор t-bid-btc4h-q1) с одним изменением:
Г-86 `market`; Г-110 `early1/2/3`; Г-129 `ttl60/300/900`; П-04 `gone50be/gone90be/gone50tr1/gone90tr1` — одним
проходом; Г-147 `--signal touch` — сигнал не ось сетки, поэтому второй проход тем же заданием суток (кэш подходов тот
же, `--touches-from study/approaches/D20`, как `p02-stage2-h9-market.sh`). Выход — `b5/p07b-t9-<клетка>/<сутки>/`,
схема `p07-cells.py`. Фильтр K ≤ 1 — потом, `p07-h9r-h14.py`.

Запуск на Steam Deck из ~/alpha: `python3 bin/p07-tk009-cells.py [--status|--submit] [--only-day D]`.
"""
import importlib.util
import os
import sys

_spec = importlib.util.spec_from_file_location(
    "p07cells", os.path.join(os.path.dirname(os.path.abspath(__file__)), "p07-cells.py"))
P = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(P)

B = P.ENTRY_B
BASE = f"{B}-pct2-tr1x1-14400"

# (каталог, имя формы в выходе сетки, сигнал)
CELLS = [("p07b-t9-market", "market-pct2-tr1x1-14400-ttl1800", "approach")]
CELLS += [(f"p07b-t9-early{x}", f"{BASE}-ttl1800-early{x}", "approach") for x in (1, 2, 3)]
CELLS += [(f"p07b-t9-ttl{x}", f"{BASE}-ttl{x}", "approach") for x in (60, 300, 900)]
CELLS += [(f"p07b-t9-{e}", f"{BASE}-ttl1800-{e}", "approach")
          for e in ("gone50be", "gone90be", "gone50tr1", "gone90tr1")]
CELLS += [("p07b-t9-touch", f"{BASE}-ttl1800", "touch")]

AXES = {
    "approach": (f"--entry-form {B} --entry-form market --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 "
                 "--entry-ttl-secs 60 --entry-ttl-secs 300 --entry-ttl-secs 900 "
                 "--exit-form none --exit-form gone50be --exit-form gone90be --exit-form gone50tr1 --exit-form gone90tr1 "
                 "--early-exit-secs off --early-exit-secs 1 --early-exit-secs 2 --early-exit-secs 3"),
    "touch": f"--entry-form {B} --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 --exit-form none",
}


def remaining(home, day):
    return [c for c in CELLS if not P.day_done(home, c[0], day)]


def build_job(bname, day, cells):
    os.makedirs(P.CELLS_DIR, exist_ok=True)
    lines = ["set -e"]
    for sig in ("approach", "touch"):
        part = [c for c in cells if c[2] == sig]
        if not part:
            continue
        cells_path = f"{P.CELLS_DIR}/t9-{sig}-{bname}-{day}.txt"
        with open(cells_path, "w", newline="\n") as f:
            for c in part:
                f.write(f"{c[1]} {P.BASE_SET}\n")
        common = P.COMMON.replace("--signal approach", f"--signal {sig}")
        out_rel = f"b5/.t9tmp-{sig}-{day}"
        lines += [
            f"rm -rf {out_rel}",
            f"{P.BIN} lob bounce-grid --root study/root-{day} --touches-from study/approaches/D20 {common} "
            f"{AXES[sig]} --set {P.BASE_SET}:{P.BASE_SPEC} --sigma-from {P.SIGMA_FROM} "
            f"--cells {cells_path} --out-dir {out_rel} > {out_rel}.log 2>&1",
            f"cp {out_rel}.log {P.CELLS_DIR}/t9-{sig}-{bname}-{day}.grid.log",
        ]
        for out_name, label, _ in part:
            dest = f"b5/{out_name}/{day}"
            lines.append(f'mkdir -p "{dest}/{P.BASE_SET}"')
            for f in ("rounds.csv", "forms.csv", "signals.csv"):
                lines.append(f'awk -F, -v f="{label}" \'NR<=2 || $3==f\' {out_rel}/{P.BASE_SET}/{f} '
                             f'> "{dest}/{P.BASE_SET}/{f}"')
            lines.append(f'[ "$(wc -l < "{dest}/{P.BASE_SET}/forms.csv")" -ge 3 ] || '
                         f'{{ echo "нет формы {label}" >&2; exit 3; }}')
            lines.append(f'touch "{dest}/.done"')
        lines.append(f"rm -rf {out_rel}")
    return "\n".join(lines) + "\n"


def main():
    args = sys.argv[1:]
    mode = "--submit" if "--submit" in args else "--status"
    only_day = args[args.index("--only-day") + 1] if "--only-day" in args else None
    total_days = total_cells = 0
    for bname, home, days in P.HOMES:
        for day in days:
            if only_day and day != only_day:
                continue
            cells = remaining(home, day)
            if not cells:
                continue
            total_days += 1
            total_cells += len(cells)
            if mode == "--submit":
                mem = P.MEM_BIG_GB if (bname, day) in P.BIG_DAYS else P.MEM_NORMAL_GB
                script = build_job(bname, day, cells)
                cmd = ["bin/q-add.sh", "--tag", "p07-t9", "--mem-gb", str(mem), "--home", home,
                       "--log", f"tmp-p07/cells-by-day/t9-{bname}-{day}.log", "--", "bash", "-c", script]
                out = P.subprocess.run(cmd, cwd=P.A, capture_output=True, text=True)
                print(f"{bname} {day}: {len(cells)} клеток, {mem} ГБ -> {out.stdout.strip() or out.stderr.strip()}")
    print(f"ИТОГО: {total_days} суток, {total_cells} клетка-суток")


if __name__ == "__main__":
    main()
