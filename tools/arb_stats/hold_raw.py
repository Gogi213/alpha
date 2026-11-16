"""Живость ноги по сырым записям книги: времена E ВСЕХ записей `b` символа (в том числе тех, что меняют только размер — в разобранных данных они сняты как повторы цены) внутри окон эпизодов.

Нужно, чтобы отличить «котировка стоит, книга живая» от «нога молчит» (обрыв, зависший поток): разобранный ряд хранит только смены лучшей цены.
Запуск: python3 hold_raw.py <need.pkl> <выход.pkl> [PAR]
need.pkl: {(биржа, символ): [(t_от_мс, t_до_мс), …]} — окна уже с запасом; выход: {(биржа, символ): отсортированный массив E (мс) записей внутри окон}.
Запись `b`: dns, lag, ET, sym, du, bid, bsz, ask, asz; E = приём − lag, приём — накопленная сумма dns по ВСЕМ строкам файла.
"""
import bisect
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
from hold_common import ROOTS5  # noqa: E402

MINRUN = 1794656000
W = {}


def merge(iv):
    out = []
    for a, b in sorted(iv):
        if out and a <= out[-1][1]:
            out[-1][1] = max(out[-1][1], b)
        else:
            out.append([a, b])
    return [x[0] for x in out], [x[1] for x in out]


def parse_file(job):
    ex, path = job
    want = W[ex]
    lo = min(v[0][0] for v in want.values())
    hi = max(v[1][-1] for v in want.values())
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    cum = 0
    res = {}
    for line in p.stdout:
        x = line.split(b"\t")
        cum += int(x[1])
        if x[0] != b"b":
            continue
        w = want.get(x[4])
        if w is None:
            continue
        e = cum // 1_000_000 - int(x[2])
        if e < lo or e > hi:
            continue
        i = bisect.bisect_right(w[0], e) - 1
        if i >= 0 and e <= w[1][i]:
            a = res.get(x[4])
            if a is None:
                a = res[x[4]] = array("q")
            a.append(e)
    p.wait()
    return ex, {s.decode(): np.frombuffer(a, "i8") for s, a in res.items()}


def main(need, out, par=4):
    need = pickle.load(open(need, "rb"))
    by_ex = {}
    for (ex, sym), iv in need.items():
        by_ex.setdefault(ex, {})[sym.encode()] = merge(iv)
    W.update(by_ex)
    jobs = []
    for ex in by_ex:
        for d in sorted(glob.glob(f"{ROOTS5[ex]}/2026-*")):
            for f in sorted(glob.glob(f"{d}/book*.tsv.zst")):
                m = re.search(r"book\d+-(\d+)\.tsv\.zst$", f)
                if m and int(m.group(1)) >= MINRUN:
                    jobs.append((ex, f))
    jobs.sort(key=lambda j: -os.path.getsize(j[1]))
    acc = {}
    t = time.time()
    with Pool(par) as pool:
        for k, (ex, d) in enumerate(pool.imap_unordered(parse_file, jobs, chunksize=1)):
            for s, a in d.items():
                acc.setdefault((ex, s), []).append(a)
            print(k + 1, len(jobs), ex, round(time.time() - t), flush=True)
    res = {k: np.unique(np.concatenate(v)) for k, v in acc.items()}
    pickle.dump(res, open(out, "wb"), protocol=4)
    print({"keys": len(res), "rows": int(sum(len(v) for v in res.values())), "s": round(time.time() - t)})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 4)
