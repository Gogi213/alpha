#!/usr/bin/env python3
"""TK-021 шаг 4: перепроверка всех бинлогов на Storage Box — `lob verify` по каждой паре (каталог, монета).

Запуск на VPS, ящик смонтирован только на чтение (/mnt/sb). Ничего на ящике не меняется; на диск VPS
кладётся копия файлов одной монеты одного каталога (WORK/<метка>-<монета>) и удаляется после сверки.

  verify-box-archive.py --out <каталог-результата> [--jobs 4] [--only <метка>,...]

Каталоги: epochs/e-*/root (метка = эпоха), root, deep, collector-2026-09-15. Результат:
  parts.csv — строка на часть (файл): метка,монета,файл,сутки,часть,байт,обновлений,разрывов,инвариантов,
              сделок,вне_диапазона,нарушений,вердикт_части (тот же тест, что `lob verify`: разрывов 0,
              инвариантов 0, нарушений·1000 < сделок);
  units.csv — строка на (метка, монета): вердикт `lob verify` (в нём ещё gaps.csv каталога), шов/потери, код, секунд.
Перезапуск продолжает: пары из units.csv пропускаются.
"""
import argparse, csv, os, re, shutil, subprocess, sys, threading, time
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor

SB = "/mnt/sb/alpha"
BIN = os.environ.get("BIN", "/opt/alpha-compute/bin/alpha-da42990-v3")
INSTR = os.environ.get("INSTR", "/opt/alpha-archive/instruments.csv")
NAME = re.compile(r"^(?P<sym>.+?)-(?P<day>\d{4}-\d{2}-\d{2})(?P<part>-p\d+)?\.binlog$")
KV = re.compile(r"(\w+)=(\d+)")

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


def part_verdict(g, i, t, v):
    return "ok" if g == 0 and i == 0 and (t == 0 or v * 1000 < t) else "fail"


def run_unit(label, src, sym, files, work, out, parts_w, units_w):
    w = f"{work}/{label}-{sym}"
    shutil.rmtree(w, ignore_errors=True)
    os.makedirs(w)
    t0 = time.time()
    try:
        shutil.copy(INSTR, f"{w}/instruments.csv")
        if os.path.exists(f"{src}/gaps.csv"):
            shutil.copy(f"{src}/gaps.csv", f"{w}/gaps.csv")
        sizes = {}
        for f in files:
            shutil.copyfile(f"{src}/{f}", f"{w}/{f}")
            sizes[f] = os.path.getsize(f"{w}/{f}")
        p = subprocess.run([BIN, "lob", "verify", "--symbol", sym, "--root", w],
                           capture_output=True, text=True)
        rows, status = [], None
        for line in p.stdout.splitlines():
            if line.startswith("verify: part="):
                name = line.split("part=")[1].split()[0]
                kv = dict((k, int(v)) for k, v in KV.findall(line.split("part_no=")[1]))
                m = NAME.match(name)
                g, i, t, v = (kv.get(k, 0) for k in ("gaps", "invariants", "trades", "violations"))
                rows.append([label, sym, name, m.group("day") if m else "", (m.group("part") or "-p1")[2:] if m else "",
                             sizes.get(name, 0), kv.get("updates", 0), g, i, t, kv.get("out_of_range", 0), v,
                             part_verdict(g, i, t, v)])
            elif line.startswith("verify: status="):
                status = dict(re.findall(r"(\w+)=(\S+)", line))
        got = {r[2] for r in rows}
        for f in files:
            if f not in got:
                rows.append([label, sym, f, "", "", sizes.get(f, 0), 0, 0, 0, 0, 0, 0, "no-output"])
        with lock:
            for r in rows:
                parts_w.writerow(r)
            units_w.writerow([label, sym, len(files), sum(sizes.values()),
                              (status or {}).get("status", "err"), (status or {}).get("gaps_csv", ""),
                              (status or {}).get("seams", ""), (status or {}).get("losses", ""),
                              (status or {}).get("step_changes", ""), p.returncode, int(time.time() - t0)])
            out["parts"].flush(); out["units"].flush()
    except Exception as e:  # копирование с ящика не удалось — пара не записывается, перезапуск возьмёт её снова
        print(f"{label} {sym}: {e!r}", file=sys.stderr)
    finally:
        shutil.rmtree(w, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--only", default="")
    ap.add_argument("--order", default="", help="метки через запятую: сначала они, остальные — после")
    ap.add_argument("--work", default="/opt/alpha-compute/tk021/w")
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    os.makedirs(a.work, exist_ok=True)
    pc, uc = f"{a.out}/parts.csv", f"{a.out}/units.csv"
    done = set()
    if os.path.exists(uc):
        with open(uc) as f:
            done = {(r[0], r[1]) for r in csv.reader(f)}
    fresh = not os.path.exists(pc)
    pf, uf = open(pc, "a", newline=""), open(uc, "a", newline="")
    parts_w, units_w = csv.writer(pf), csv.writer(uf)
    if fresh:
        parts_w.writerow("метка монета файл сутки часть байт обновлений разрывов инвариантов сделок вне_диапазона нарушений вердикт_части".split())
    only = set(filter(None, a.only.split(",")))
    jobs = []
    for label, src in sources().items():
        if only and label not in only:
            continue
        by = defaultdict(list)
        for f in sorted(os.listdir(src)):
            m = NAME.match(f)
            if m:
                by[m.group("sym")].append(f)
        for sym, files in by.items():
            if (label, sym) not in done:
                jobs.append((label, src, sym, files))
    rank = {l: i for i, l in enumerate(filter(None, a.order.split(",")))}
    jobs.sort(key=lambda j: (rank.get(j[0], len(rank)), -len(j[3])))
    print(f"{time.strftime('%FT%TZ', time.gmtime())} пар к проверке: {len(jobs)} (уже есть: {len(done)})", flush=True)
    out = {"parts": pf, "units": uf}
    with ThreadPoolExecutor(a.jobs) as ex:
        futs = [ex.submit(run_unit, l, s, y, fs, a.work, out, parts_w, units_w) for l, s, y, fs in jobs]
        for n, fu in enumerate(futs, 1):
            fu.result()
            if n % 25 == 0:
                print(f"{time.strftime('%FT%TZ', time.gmtime())} готово {n}/{len(jobs)}", flush=True)
    print(f"{time.strftime('%FT%TZ', time.gmtime())} конец", flush=True)


main()
