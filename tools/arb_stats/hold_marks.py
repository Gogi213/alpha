"""Mark-цена, индекс и фандинг по символам: ряд «последняя mark в 10-секундном окне» и выплаты фандинга (время начисления, ставка) из записей `m` сырых файлов.

Запуск: python3 hold_marks.py <t0_мс> <t1_мс> <выход.pkl> [PAR]; переменные: EXCH — биржи через запятую (по умолчанию все пять), MINRUN / MAXRUN — границы времени старта запуска сборщика в имени файла (с).
Запись `m`: dns, lag, sym, p (mark), ap, P, i (индекс), r (ставка), T (следующее начисление, мс). Время записи E = приём − lag, приём — накопленная сумма dns по ВСЕМ строкам файла (как в parse_book).
Выплата: когда T символа меняется на следующее, предыдущее T считается состоявшимся начислением со ставкой r последней записи перед сменой.
Выход: {биржа: {символ: {"b": bucket10s (int64, E // 10000), "p": mark, "pay": [[T_мс, ставка], …]}}}.
"""
import glob
import os
import pickle
import re
import subprocess
import sys
import time
from array import array
from multiprocessing import Pool
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from hold_common import EX5, ROOTS5  # noqa: E402

BUCKET_MS = 10_000
MINRUN = int(os.environ.get("MINRUN", 1794656000))     # запуски сборщика по времени старта в имени файла: [MINRUN, MAXRUN)
MAXRUN = int(os.environ.get("MAXRUN", 10 ** 12))


def parse_file(job):
    ex, path, t0, t1 = job
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    cum = 0
    res, cur, last = {}, {}, {}
    for line in p.stdout:
        x = line.split(b"\t")
        cum += int(x[1])
        if x[0] != b"m" or len(x) < 10:
            continue
        e = cum // 1_000_000 - int(x[2])
        if e < t0 - 3_600_000 or e > t1 + 3_600_000:
            continue
        s = x[3].decode()
        try:
            pr = float(x[4])
        except ValueError:
            continue
        b = e // BUCKET_MS
        r = res.get(s)
        if r is None:
            r = res[s] = (array("q"), array("d"), [])
        c = cur.get(s)
        if c is not None and c[0] != b:
            r[0].append(c[0])
            r[1].append(c[1])
        cur[s] = (b, pr)
        try:
            tf = int(x[9]) if x[9] else 0
            rate = float(x[8]) if x[8] else None
        except ValueError:
            tf, rate = 0, None
        lt = last.get(s)
        if tf and lt is not None and lt[0] and tf != lt[0] and lt[1] is not None:
            r[2].append((lt[0], lt[1]))
        if tf:
            last[s] = (tf, rate if rate is not None else (lt[1] if lt else None))
    p.wait()
    for s, c in cur.items():
        res[s][0].append(c[0])
        res[s][1].append(c[1])
    return ex, {s: (np.frombuffer(a[0], "i8"), np.frombuffer(a[1], "f8"), a[2]) for s, a in res.items()}


def main(t0, t1, out, par=4):
    jobs = []
    for ex in os.environ.get("EXCH", ",".join(EX5)).split(","):
        for d in sorted(glob.glob(f"{ROOTS5[ex]}/2026-*")):
            for f in sorted(glob.glob(f"{d}/mark*.tsv.zst")):
                m = re.search(r"mark\d+-(\d+)\.tsv\.zst$", f)
                if m and MINRUN <= int(m.group(1)) < MAXRUN:
                    jobs.append((ex, f, t0, t1))
    jobs.sort(key=lambda j: -os.path.getsize(j[1]))
    acc = {ex: {} for ex in EX5}
    t = time.time()
    with Pool(par) as pool:
        for k, (ex, d) in enumerate(pool.imap_unordered(parse_file, jobs, chunksize=1)):
            for s, (b, p_, pay) in d.items():
                a = acc[ex].setdefault(s, {"b": [], "p": [], "pay": []})
                a["b"].append(b)
                a["p"].append(p_)
                a["pay"] += pay
            print(k + 1, len(jobs), ex, round(time.time() - t), flush=True)
    final = {}
    for ex, m in acc.items():
        final[ex] = {}
        for s, a in m.items():
            b, p_ = np.concatenate(a["b"]), np.concatenate(a["p"])
            o = np.argsort(b, kind="stable")
            b, p_ = b[o], p_[o]
            keep = np.r_[b[1:] != b[:-1], True]            # последняя запись окна
            final[ex][s] = {"b": b[keep], "p": p_[keep], "pay": sorted(set(a["pay"]))}
    pickle.dump(final, open(out, "wb"), protocol=4)
    print({ex: len(m) for ex, m in final.items()}, round(time.time() - t))


if __name__ == "__main__":
    main(int(sys.argv[1]), int(sys.argv[2]), sys.argv[3], int(sys.argv[4]) if len(sys.argv) > 4 else 4)
