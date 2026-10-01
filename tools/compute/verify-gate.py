#!/usr/bin/env python3
"""TK-021: гейт ускорения `lob verify` — прежний и новый бинарник на одних и тех же файлах ящика, вывод побайтно.

Запуск на VPS (ящик смонтирован ro в /mnt/sb):
  verify-gate.py --old <бинарник> --new <бинарник> --out <каталог> [--per-dir 2] [--files 3] [--jobs 2]

Пары: фиксированные (HYPE 2026-06-05; листинговые сутки с пустым первым снимком и «seq:-1»; сутки со снимком до
полуночи) + по `--per-dir` случайных монет из каждого каталога ящика (эпохи, root, deep, collector-2026-09-15),
у каждой пары `--files` суток. Для пары файлы копируются один раз, оба бинарника идут по одной копии, вывод
(stdout+stderr) и код возврата сравниваются побайтно; время — процессорное (user+sys), нагрузка VPS не мешает.
Результат: gate.csv (по паре), <метка>-<монета>.{old,new}.txt, итог в stdout.
"""
import argparse, csv, os, random, re, shutil, subprocess, sys, threading, time
from concurrent.futures import ThreadPoolExecutor

SB = "/mnt/sb/alpha"
INSTR = "/opt/alpha-archive/instruments.csv"
NAME = re.compile(r"^(?P<sym>.+?)-(?P<day>\d{4}-\d{2}-\d{2})(?P<part>-p\d+)?\.binlog$")
FIXED = [  # (метка, монета, сутки или None)
    ("e-jun", "HYPEUSDT", ["2026-06-04", "2026-06-05", "2026-06-06"]),
    ("e-apr", "GOOGLUSDT", ["2026-04-27"]),
    ("e-jul", "SKHYUSDT", ["2026-07-11"]),
    ("e-may", "NVDAUSDT", ["2026-05-06"]),
    ("e-mar", "XAGUSDT", ["2026-03-09"]),
    ("e-feb", "UNIUSDT", ["2026-02-07"]),
    ("e-jan", "UNIUSDT", ["2026-01-14"]),
    ("e-apr", "ATOMUSDT", ["2026-04-24"]),
]
lock = threading.Lock()


def sources():
    out = {}
    ep = f"{SB}/epochs"
    for e in sorted(os.listdir(ep)):
        if os.path.isdir(f"{ep}/{e}/root"):
            out[e] = f"{ep}/{e}/root"
    for d in ("root", "deep", "collector-2026-09-15"):
        if os.path.isdir(f"{SB}/{d}"):
            out[d] = f"{SB}/{d}"
    return out


def cpu_run(binary, w, sym, outfile):
    t0 = time.time()
    with open(outfile, "wb") as f:
        p = subprocess.Popen([binary, "lob", "verify", "--symbol", sym, "--root", w], stdout=f, stderr=subprocess.STDOUT)
        _, st, ru = os.wait4(p.pid, 0)
    return os.waitstatus_to_exitcode(st), ru.ru_utime + ru.ru_stime, time.time() - t0


def one(args, label, src, sym, files, rows):
    w = f"{args.work}/gate-{label}-{sym}"
    shutil.rmtree(w, ignore_errors=True)
    os.makedirs(w)
    try:
        shutil.copy(INSTR, f"{w}/instruments.csv")
        if os.path.exists(f"{src}/gaps.csv"):
            shutil.copy(f"{src}/gaps.csv", f"{w}/gaps.csv")
        size = 0
        for f in files:
            shutil.copyfile(f"{src}/{f}", f"{w}/{f}")
            size += os.path.getsize(f"{w}/{f}")
        po, pn = f"{args.out}/{label}-{sym}.old.txt", f"{args.out}/{label}-{sym}.new.txt"
        co, cpu_o, wall_o = cpu_run(args.old, w, sym, po)
        cn, cpu_n, wall_n = cpu_run(args.new, w, sym, pn)
        a, b = open(po, "rb").read(), open(pn, "rb").read()
        same = a == b and co == cn
        text = a.decode("utf-8", "replace")
        upd = sum(int(m) for m in re.findall(r"^verify: part=.*? updates=(\d+)", text, re.M))
        with lock:
            rows.append([label, sym, len(files), size, upd, "да" if same else "НЕТ", co, cn,
                         round(cpu_o, 1), round(cpu_n, 1), round(wall_o, 1), round(wall_n, 1)])
            print(f"{label} {sym}: {len(files)} файлов, {size >> 20} МБ, {'совпало' if same else 'РАЗНИЦА'}, "
                  f"cpu {cpu_o:.1f} → {cpu_n:.1f} с", flush=True)
    finally:
        shutil.rmtree(w, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--old", required=True)
    ap.add_argument("--new", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--per-dir", type=int, default=2)
    ap.add_argument("--files", type=int, default=3)
    ap.add_argument("--jobs", type=int, default=2)
    ap.add_argument("--work", default="/opt/alpha-compute/tk021/w")
    ap.add_argument("--seed", type=int, default=21)
    args = ap.parse_args()
    os.makedirs(args.out, exist_ok=True)
    os.makedirs(args.work, exist_ok=True)
    rnd = random.Random(args.seed)
    src = sources()
    jobs, seen = [], set()
    for label, sym, days in FIXED:
        names = [f for f in sorted(os.listdir(src[label])) if (m := NAME.match(f)) and m.group("sym") == sym
                 and m.group("day") in days]
        if names:
            jobs.append((label, src[label], sym, names))
            seen.add((label, sym))
        else:
            print(f"нет файлов: {label} {sym} {days}", file=sys.stderr)
    for label, d in src.items():
        by = {}
        for f in sorted(os.listdir(d)):
            m = NAME.match(f)
            if m and os.path.exists(f"{d}/{f}"):
                by.setdefault(m.group("sym"), []).append(f)
        syms = sorted(s for s in by if (label, s) not in seen)
        for sym in rnd.sample(syms, min(args.per_dir, len(syms))):
            fs = by[sym]
            pick = sorted(rnd.sample(fs, min(args.files, len(fs))))
            jobs.append((label, d, sym, pick))
    print(f"пар: {len(jobs)}", flush=True)
    rows = []
    with ThreadPoolExecutor(args.jobs) as ex:
        for fu in [ex.submit(one, args, l, s, y, fs, rows) for l, s, y, fs in jobs]:
            fu.result()
    rows.sort()
    with open(f"{args.out}/gate.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow("метка монета файлов байт обновлений совпало код_старый код_новый cpu_старый_с cpu_новый_с wall_старый_с wall_новый_с".split())
        w.writerows(rows)
    same = sum(r[5] == "да" for r in rows)
    co, cn = sum(r[8] for r in rows), sum(r[9] for r in rows)
    upd = sum(r[4] for r in rows)
    print(f"ИТОГО: пар {len(rows)}, совпало {same}, обновлений {upd}, cpu {co:.0f} → {cn:.0f} с (×{co / max(cn, 0.1):.1f})")


main()
