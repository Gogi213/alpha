"""Живость ноги по сырым записям книги: времена E ВСЕХ записей `b` символа (в том числе тех, что меняют только размер — в разобранных данных они сняты как повторы цены) внутри окон эпизодов.

Нужно, чтобы отличить «котировка стоит, книга живая» от «нога молчит» (обрыв, зависший поток): разобранный ряд хранит только смены лучшей цены.
Запуск: python3 hold_raw.py <need.pkl> <выход.pkl> [PAR]; результат каждого файла сразу пишется в <выход.pkl>.parts/ и при повторном запуске не пересчитывается (при смене need.pkl каталог очистить)
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


def file_overlaps(ex, path, iv_starts, iv_ends, pad_ms=120_000):
    """Файл сборщика покрывает [старт запуска из имени; mtime] — пересекается ли он с каким-нибудь окном биржи (объединённые окна всех символов)."""
    m = re.search(r"book\d+-(\d+)\.tsv\.zst$", path)
    lo, hi = int(m.group(1)) * 1000 - pad_ms, int(os.path.getmtime(path) * 1000) + pad_ms
    i = bisect.bisect_right(iv_ends, lo)
    return i < len(iv_starts) and iv_starts[i] <= hi


def parse_file(job):
    ex, path = job
    want = W[ex]
    lo = min(v[0][0] for v in want.values())
    hi = max(v[1][-1] for v in want.values())
    part = Path(W["parts"], f"{ex}-{Path(path).parent.name}-{Path(path).name}.pkl")
    if part.exists():
        return ex, None
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    cum = 0
    res = {}
    for line in p.stdout:
        if not line.endswith(b"\n"):        # недописанная последняя строка живого файла — как в parse_inc: не читаем
            break
        x = line.split(b"\t")
        cum += int(x[1])
        if x[0] != b"b" or len(x) < 10:
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
    out = {s.decode(): np.frombuffer(a, "i8") for s, a in res.items()}
    tmp = part.with_suffix(".tmp")
    pickle.dump(out, open(tmp, "wb"), protocol=4)
    tmp.rename(part)
    return ex, None


def main(need, out, par=4):
    need = pickle.load(open(need, "rb"))
    by_ex = {}
    for (ex, sym), iv in need.items():
        by_ex.setdefault(ex, {})[sym.encode()] = merge(iv)
    W.update(by_ex)
    parts = Path(out + ".parts")
    parts.mkdir(parents=True, exist_ok=True)
    W["parts"] = str(parts)
    jobs = []
    for ex in by_ex:
        for d in sorted(glob.glob(f"{ROOTS5[ex]}/2026-*")):
            for f in sorted(glob.glob(f"{d}/book*.tsv.zst")):
                m = re.search(r"book\d+-(\d+)\.tsv\.zst$", f)
                if m and int(m.group(1)) >= MINRUN:
                    jobs.append((ex, f))
    allw = {}
    for (ex, sym), iv in need.items():
        allw.setdefault(ex, []).extend(iv)
    cover = {ex: merge(iv) for ex, iv in allw.items()}
    jobs = [j for j in jobs if file_overlaps(j[0], j[1], *cover[j[0]])]
    jobs.sort(key=lambda j: -os.path.getsize(j[1]))
    acc = {}
    t = time.time()
    with Pool(par) as pool:
        for k, (ex, _) in enumerate(pool.imap_unordered(parse_file, jobs, chunksize=1)):
            print(k + 1, len(jobs), ex, round(time.time() - t), flush=True)
    for j in jobs:
        ex, path = j
        for s, a in pickle.load(open(Path(W["parts"], f"{ex}-{Path(path).parent.name}-{Path(path).name}.pkl"), "rb")).items():
            acc.setdefault((ex, s), []).append(a)
    res = {k: np.unique(np.concatenate(v)) for k, v in acc.items()}
    pickle.dump(res, open(out, "wb"), protocol=4)
    print({"keys": len(res), "rows": int(sum(len(v) for v in res.values())), "s": round(time.time() - t)})


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 4)
