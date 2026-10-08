#!/usr/bin/env python3
"""П-12 R1 (TK-063): клетки R1 TK-064 -> busy-replay по каждой клетке -> portfolio-sim по месяцу.

  p12-r1-psim.py MON   (MON = feb..oct; юнит alsched, 1 ядро)

1. Слияние деревьев пула /data/tk064/pool/m-<MON>/{signals,signals-retry}/<сутки>/<набор>/ по ключу (symbol, day_utc, signal_index),
   первым побеждает signals (как load_month в tk064-r1cells.py) -> /data/tk063r1/src/<мес>/<сутки>/<набор>/{signals,rounds}.csv.
2. busy-replay.py: B1 (все сигналы) и каждая клетка (--keep cells/<MON>/<клетка>.csv; ключ symbol,day_utc,signal_index) ->
   /data/tk063r1/b/<мес>/<сутки>/<клетка>/rounds.csv (имя клетки = «набор» для portfolio-sim). Пустая клетка (0 строк) — не считается,
   её имя пишется в /data/tk063r1/empty-<мес>.txt.
3. portfolio-sim.py: все клетки + B1 одним проходом, без потолка (cap0) и B2 = 3 (cap3), closes-out; депозит 2500, позиция 500 — как R2.
"""
import csv, glob, os, shutil, subprocess, sys

O = "/data/tk063r1"
T = "/data/tk083/tools"
POOL = "/data/tk064/pool"
CELLS = "/data/tk064/r1/cells"
MONS = {"jan": "01", "feb": "02", "mar": "03", "apr": "04", "may": "05", "jun": "06", "jul": "07", "aug": "08", "sep": "09", "oct": "10"}
SET = "t-bid-btc4h-q1"


def split(path):
    with open(path, encoding="utf-8", newline="") as fh:
        lines = fh.read().splitlines(keepends=True)
    com = [l for l in lines if l.startswith("#")]
    body = [l for l in lines if not l.startswith("#")]
    return com, body[0], body[1:]


def merge(mon, label):
    """-> форма B1 (одна на месяц)."""
    forms = set()
    days = {}
    for sub in ("signals", "signals-retry"):
        for d in sorted(os.listdir(f"{POOL}/m-{mon}/{sub}")) if os.path.isdir(f"{POOL}/m-{mon}/{sub}") else []:
            p = f"{POOL}/m-{mon}/{sub}/{d}/{SET}"
            if os.path.isfile(f"{p}/signals.csv") and os.path.isfile(f"{p}/rounds.csv"):
                days.setdefault(d, []).append(p)
    for d, srcs in sorted(days.items()):
        dst = f"{O}/src/{label}/{d}/{SET}"
        os.makedirs(dst, exist_ok=True)
        for name in ("signals.csv", "rounds.csv"):
            com0, head0, rows0 = split(f"{srcs[0]}/{name}")
            out, seen = list(rows0), set()
            hr = head0.rstrip("\r\n").split(",")
            ki = [hr.index(c) for c in ("symbol", "day_utc", "signal_index")]
            keyof = lambda line: tuple(next(csv.reader([line]))[i] for i in ki)
            seen = {keyof(l) for l in rows0}
            for s in srcs[1:]:
                com, head, rows = split(f"{s}/{name}")
                if head != head0:
                    sys.exit(f"{s}/{name}: шапка колонок не совпала с {srcs[0]}")
                for l in rows:
                    k = keyof(l)
                    if k not in seen:
                        seen.add(k)
                        out.append(l)
            with open(f"{dst}/{name}", "w", encoding="utf-8", newline="") as fh:
                fh.writelines(com0 + [head0] + out)
            if name == "rounds.csv":
                fi = hr.index("form")
                forms |= {next(csv.reader([l]))[fi] for l in out}
    if len(forms) != 1:
        sys.exit(f"{mon}: форм B1 не одна: {sorted(forms)}")
    return forms.pop()


def busy(src, out, keep=None):
    cmd = ["python3", f"{T}/busy-replay.py", src, out] + (["--keep", keep] if keep else [])
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode:
        sys.exit(f"busy-replay {keep or 'B1'}: {r.stderr[-400:]}")


def main():
    mon = sys.argv[1]
    label = f"2026-{MONS[mon]}"
    os.makedirs(O, exist_ok=True)
    os.chdir(O)
    for f in (f"psim-{mon}.done", f"fail-{mon}.txt"):
        if os.path.exists(f):
            os.remove(f)
    shutil.rmtree(f"src/{label}", ignore_errors=True)
    form = merge(mon, label)
    shutil.rmtree(f"b/{label}", ignore_errors=True)
    # B1
    busy(f"src/{label}", f"tmp/{mon}/B1")
    names = ["B1"]
    for d in os.listdir(f"tmp/{mon}/B1"):
        if os.path.isdir(f"tmp/{mon}/B1/{d}/{SET}"):
            os.makedirs(f"b/{label}/{d}", exist_ok=True)
            os.rename(f"tmp/{mon}/B1/{d}/{SET}", f"b/{label}/{d}/B1")
    empty = []
    for cf in sorted(glob.glob(f"{CELLS}/{mon}/*.csv")):
        cell = os.path.basename(cf)[:-4]
        with open(cf, encoding="utf-8") as fh:
            n = sum(1 for _ in fh) - 1
        if n <= 0:
            empty.append(cell)
            continue
        busy(f"src/{label}", f"tmp/{mon}/{cell}", cf)
        names.append(cell)
        for d in os.listdir(f"tmp/{mon}/{cell}"):
            if os.path.isdir(f"tmp/{mon}/{cell}/{d}/{SET}"):
                os.makedirs(f"b/{label}/{d}", exist_ok=True)
                os.rename(f"tmp/{mon}/{cell}/{d}/{SET}", f"b/{label}/{d}/{cell}")
        shutil.rmtree(f"tmp/{mon}/{cell}")
    shutil.rmtree(f"tmp/{mon}", ignore_errors=True)
    with open(f"empty-{mon}.txt", "w", encoding="utf-8") as fh:
        fh.write("".join(c + "\n" for c in empty))
    with open(f"form-{mon}.txt", "w", encoding="utf-8") as fh:
        fh.write(form + "\n")
    # регим-данные (btc1h) — как у R2: home = дерево с study/regime; берём ссылку из R2
    if not os.path.exists("b/study"):
        os.symlink("/data/p12r2/v-b/study", "b/study") if os.path.exists("/data/p12r2/v-b/study") else None
    args = []
    for c in names:
        args += ["--variant", f"{c}={c}/{form}"]
    for cap in (0, 3):
        r = subprocess.run(["python3", f"{T}/portfolio-sim.py", "--epoch", f"{label}={O}/b:{label}", *args,
                            "--deposit-usd", "2500", "--position-usd", "500", "--max-pos", str(cap),
                            "--json", f"psim-cap{cap}-{mon}.json", "--closes-out", f"closes-cap{cap}-{mon}.json"],
                           capture_output=True, text=True)
        open(f"psim-cap{cap}-{mon}.txt", "w", encoding="utf-8").write(r.stdout)
        open(f"psim-cap{cap}-{mon}.log", "w", encoding="utf-8").write(r.stderr)
        if r.returncode:
            open(f"fail-{mon}.txt", "a").write(f"FAIL cap{cap} {mon}\n")
    open(f"psim-{mon}.done", "w").close()


if __name__ == "__main__":
    main()
