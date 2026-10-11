#!/usr/bin/env python3
"""П-08 (TK-013): один вызов на сутки через `alpha-gridq` — авг + сен + июль, бинарник TK-012 `alpha-a5d4e3a`.

Сутки = одно задание q-add (compute по порядку, не разными заданиями — gridq не держит порядок между ними):
  1) `lob touches` новым бинарником → кэш подходов D20 с колонками П-08 во ВРЕМЕННЫЙ каталог
     `tmp-p08/cache/<дом>-<сутки>/D20/<сутки>/` (+ `--minute-flow` → `tmp-p08/mf/<сутки>/`, Г-140, ~4 МБ/сутки);
  2) `bounce-grid --cells` (одна клетка — B1 = `p07b-base`, форма Г-85б, флаги `p07-cells.COMMON`, σ своего дома)
     с `--p08-cols` на этом кэше → `b5/p08-b1/<сутки>/t-bid-btc4h-q1/{rounds,forms,signals}.csv`;
  3) сверка: тела `rounds.csv`/`forms.csv` = `b5/p07b-base/<сутки>/…` (без строки `#`), если база есть →
     `b5/p08-b1/<сутки>/.same` или `.diff` (стоп задания, код 4);
  4) временный кэш суток удаляется полным путём (≈ 0,4 ГБ/сутки × 85 не влезают на диск разом).
Все клетки П-08 — фильтр строк `signals.csv` этой B1 (`p08-feats.py` → `p08-run.py`, §12 п. 4).

    python3 p08-cells.py --status
    python3 p08-cells.py --submit --only-day 2026-08-03    # проба
    python3 p08-cells.py --submit                           # всё оставшееся
"""
import importlib.util
import os
import subprocess
import sys

A = os.path.expanduser("~/alpha")
HERE = os.path.dirname(os.path.abspath(__file__))
BIN = "bin/alpha-a5d4e3a"  # TK-012, гейт tmp-t38/g12 зелёный (28.09 03:30)
OUT = "p08-b1"


def load_pc():
    for d in (HERE, os.path.join(A, "tmp-p07"), os.path.join(A, "bin")):
        p = os.path.join(d, "p07-cells.py")
        if os.path.exists(p):
            spec = importlib.util.spec_from_file_location("pc", p)
            m = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(m)
            return m
    sys.exit("нет p07-cells.py")


pc = load_pc()
JUL = f"{A}/epochs/e-jul"
HOMES = pc.HOMES + [("jul", JUL, [f"2026-07-{d:02d}" for d in range(1, 32)])]
SIGMA = {"aug": pc.SIGMA_FROM, "sep1": pc.SIGMA_FROM, "sep2": pc.SIGMA_FROM, "jul": f"{JUL}/study/sigma240"}
MEM_GB = 3.0


def build_job(bname, home, day):
    c = pc.cell("p07b-base", pc.ENTRY_B)
    cells_path = f"{A}/tmp-p08/cells/{bname}-{day}.txt"
    os.makedirs(os.path.dirname(cells_path), exist_ok=True)
    with open(cells_path, "w", newline="\n") as f:
        f.write(f"{pc.label(c)} {pc.BASE_SET}\n")
    cache = f"{A}/tmp-p08/cache/{bname}-{day}"
    mf = f"{A}/tmp-p08/mf/{day}"
    dest = f"b5/{OUT}/{day}"
    base = f"b5/p07b-base/{day}/{pc.BASE_SET}"
    axes = (f"--entry-form {pc.ENTRY_B} --stop-form pct2 --take-form tr1x1 --deadline-secs 14400 --exit-form none "
            f"--set {pc.BASE_SET}:{pc.BASE_SPEC} --sigma-from {SIGMA[bname]}")
    return f"""set -e
rm -rf "{cache}" "{dest}"
mkdir -p "{cache}/D20/{day}" "{mf}" "{dest}"
# монеты без сверки `ok` (LSKUSDT 14.09 — `excluded`, В-112) — не считаются, как в базе p07b-base
for s in $(cat study/touches/{day}/symbols.txt); do [ "$(cat study/root-{day}/verify-$s.status 2>/dev/null)" = ok ] && echo $s || echo "вне сверки: $s" >&2; done > {cache}/symbols.txt
xargs -a {cache}/symbols.txt -P 3 -I{{}} sh -c "nice -n 5 {A}/{BIN} lob touches --root study/root-{day} --symbol {{}} --h3-mode notional --h3-usd 10000 --approach-bps 20 --minute-flow {mf} --out {cache}/D20/{day}/touches-{{}}.csv > {cache}/D20/{day}/{{}}.log 2>&1"
{A}/{BIN} lob bounce-grid --root study/root-{day} --touches-from {cache}/D20 {pc.COMMON} {axes} --p08-cols \
  --cells {cells_path} --out-dir {dest} > {dest}.grid.log 2>&1
[ "$(wc -l < {dest}/{pc.BASE_SET}/forms.csv)" -ge 3 ] || {{ echo "нет формы" >&2; exit 3; }}
if [ -f {base}/rounds.csv ]; then
  ok=1
  for f in rounds.csv forms.csv; do cmp -s <(grep -v '^#' {base}/$f) <(grep -v '^#' {dest}/{pc.BASE_SET}/$f) || ok=0; done
  if [ $ok = 1 ]; then touch {dest}/.same; else touch {dest}/.diff; rm -rf "{cache}"; exit 4; fi
fi
rm -rf "{cache}"
touch {dest}/.done
"""


def main():
    args = sys.argv[1:]
    mode = args[0] if args else "--status"
    only = args[args.index("--only-day") + 1] if "--only-day" in args else None
    n = 0
    for bname, home, days in HOMES:
        for day in days:
            if only and day != only:
                continue
            if os.path.exists(f"{home}/b5/{OUT}/{day}/.done"):
                continue
            n += 1
            if mode == "--submit":
                script = build_job(bname, home, day)
                cmd = ["bin/q-add.sh", "--tag", "p08-cells", "--mem-gb", str(MEM_GB), "--home", home,
                       "--log", f"tmp-p08/cells/{bname}-{day}.log", "--", "bash", "-c", script]
                r = subprocess.run(cmd, cwd=A, capture_output=True, text=True)
                print(f"{bname} {day}: {(r.stdout or r.stderr).strip()}")
    print(f"ИТОГО осталось суток: {n}")


if __name__ == "__main__":
    main()
