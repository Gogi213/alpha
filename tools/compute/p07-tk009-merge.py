#!/usr/bin/env python3
"""TK-009 × П-08 (владелец 28.09 ~03:50 через CEO: «если есть выигрыш по времени — делай»): слить клетки TK-009 в
прогон П-08 — для суток авг/сен, где ОБА задания ещё в `queue/pending` (p08-cells и p07-t9), одно задание на сутки:

  1) `lob touches` бинарником TK-012 во временный кэш (как `p08-cells.py`);
  2) ОДИН `bounce-grid --cells` на этом кэше с `--p08-cols`: B1 (`p07b-base`) + 10 клеток TK-009 на approach
     (оси — объединение; бинарник TK-012 без флагов П-08 побайтно = e74f200-v3, гейт g12);
  3) B1 → `b5/p08-b1/<сутки>/` + сверка `.same/.diff` с `b5/p07b-base` (как `p08-cells.py`); клетки TK-009 →
     `b5/p07b-t9-<клетка>/<сутки>/` + `.done`;
  4) клетка Г-147 (`--signal touch`, не совмещается с approach) — второй проход тем же заданием, как
     `p07-tk009-cells.py`.
Сутки, у которых одно из заданий уже стартовало/готово, не трогаются. Снятые задания — в `tmp-p08/dropped-merge/`.

    python3 bin/p07-tk009-merge.py --status | --submit [--only-day D] [--prio N]
"""
import glob
import importlib.util
import os
import shutil
import subprocess
import sys

A = os.path.expanduser("~/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, fname):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, fname))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


P8 = load("p08", "p08-cells.py")
T9 = load("t9", "p07-tk009-cells.py")
pc = P8.pc
Q = f"{A}/queue/pending"
DROP = f"{A}/tmp-p08/dropped-merge"


def pending_job(pattern):
    for f in sorted(os.listdir(Q)):
        if f.endswith(".job") and pattern in open(os.path.join(Q, f), encoding="utf-8", errors="replace").read():
            return os.path.join(Q, f)
    return None


def build_job(bname, day):
    b1 = pc.label(pc.cell("p07b-base", pc.ENTRY_B))
    appr = [c for c in T9.CELLS if c[2] == "approach"]
    touch = [c for c in T9.CELLS if c[2] == "touch"]
    cells_path = f"{A}/tmp-p08/cells/m-{bname}-{day}.txt"
    os.makedirs(os.path.dirname(cells_path), exist_ok=True)
    with open(cells_path, "w", newline="\n") as f:
        f.write(f"{b1} {pc.BASE_SET}\n")
        for c in appr:
            f.write(f"{c[1]} {pc.BASE_SET}\n")
    cache = f"{A}/tmp-p08/cache/{bname}-{day}"
    mf = f"{A}/tmp-p08/mf/{day}"
    dest = f"b5/{P8.OUT}/{day}"
    tmp = f"b5/.m-{day}"
    base = f"b5/p07b-base/{day}/{pc.BASE_SET}"
    S = pc.BASE_SET
    axes = f"{T9.AXES['approach']} --set {S}:{pc.BASE_SPEC} --sigma-from {P8.SIGMA[bname]}"
    split = []
    for out_dir, label in [(dest, b1)] + [(f"b5/{c[0]}/{day}", c[1]) for c in appr]:
        split.append(f'mkdir -p "{out_dir}/{S}"')
        for fn in ("rounds.csv", "forms.csv", "signals.csv"):
            split.append(f'awk -F, -v f="{label}" \'NR<=2 || $3==f\' {tmp}/{S}/{fn} > "{out_dir}/{S}/{fn}"')
        split.append(f'[ "$(wc -l < "{out_dir}/{S}/forms.csv")" -ge 3 ] || {{ echo "нет формы {label}" >&2; exit 3; }}')
    t9_done = "\n".join(f'touch "b5/{c[0]}/{day}/.done"' for c in appr)
    split_s = "\n".join(split)
    return f"""set -e
rm -rf "{cache}" "{dest}" "{tmp}"
mkdir -p "{cache}/D20/{day}" "{mf}" "{dest}"
xargs -a study/touches/{day}/symbols.txt -P 3 -I{{}} sh -c "nice -n 5 {A}/{P8.BIN} lob touches --root study/root-{day} --symbol {{}} --h3-mode notional --h3-usd 10000 --approach-bps 20 --minute-flow {mf} --out {cache}/D20/{day}/touches-{{}}.csv > {cache}/D20/{day}/{{}}.log 2>&1"
{A}/{P8.BIN} lob bounce-grid --root study/root-{day} --touches-from {cache}/D20 {pc.COMMON} {axes} --p08-cols \\
  --cells {cells_path} --out-dir {tmp} > {dest}.grid.log 2>&1
{split_s}
if [ -f {base}/rounds.csv ]; then
  ok=1
  for f in rounds.csv forms.csv; do cmp -s <(grep -v '^#' {base}/$f) <(grep -v '^#' {dest}/{S}/$f) || ok=0; done
  if [ $ok = 1 ]; then touch {dest}/.same; else touch {dest}/.diff; rm -rf "{cache}" "{tmp}"; exit 4; fi
fi
{t9_done}
rm -rf "{cache}" "{tmp}"
touch {dest}/.done
{T9.build_job(bname, day, touch)}"""


def main():
    args = sys.argv[1:]
    mode = "--submit" if "--submit" in args else "--status"
    only = args[args.index("--only-day") + 1] if "--only-day" in args else None
    prio = args[args.index("--prio") + 1] if "--prio" in args else "5"
    n = skipped = 0
    for bname, home, days in pc.HOMES:
        for day in days:
            if only and day != only:
                continue
            j8 = pending_job(f"b5/p08-b1/{day}")
            j9 = pending_job(f"t9tmp-approach-{day}")
            if not (j8 and j9):
                skipped += 1
                continue
            n += 1
            if mode != "--submit":
                continue
            os.makedirs(DROP, exist_ok=True)
            try:  # снять оба атомарно; стартовавшее за это время — вернуть второе на место
                shutil.move(j8, DROP)
            except (FileNotFoundError, OSError):
                skipped += 1
                continue
            try:
                shutil.move(j9, DROP)
            except (FileNotFoundError, OSError):
                shutil.move(os.path.join(DROP, os.path.basename(j8)), Q)
                skipped += 1
                continue
            mem = pc.MEM_BIG_GB if (bname, day) in pc.BIG_DAYS else P8.MEM_GB
            cmd = ["bin/q-add.sh", "--tag", "p08t9-cells", "--prio", prio, "--mem-gb", str(mem), "--home", home,
                   "--log", f"tmp-p08/cells/m-{bname}-{day}.log", "--", "bash", "-c", build_job(bname, day)]
            r = subprocess.run(cmd, cwd=A, capture_output=True, text=True)
            print(f"{bname} {day}: {(r.stdout or r.stderr).strip()}")
    print(f"слито суток: {n}, не тронуто (одно из заданий не в pending): {skipped}")


if __name__ == "__main__":
    main()
