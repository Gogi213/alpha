"""Сделки по символам пяти бирж в окнах по 10 с: число сделок и число сделок «тейкер продаёт» (флаг m = 1 — покупатель мейкер) — для признака «поток сделок» в hold_stay.py.

Запись `t`: dns, lag, ET, sym, da, p, q, lf, f, m, nq (как parse_trades); E = приём − lag, приём — накопленная сумма dns по ВСЕМ строкам файла. Читаются все файлы trade*.tsv.zst всех запусков, символы по суффиксу USDT (как hold_parse).
Запуск: python3 hold_trades.py <выход.pkl> [PAR]; выход: {(биржа, символ): (окно10с int64 (E // 10000, по возрастанию), сделок int32, из них тейкер-продажа int32)}.
"""
import glob
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
from hold_parse import SUFFIX  # noqa: E402

BUCKET_MS = 10_000


def parse_file(job):
    ex, path = job
    suffix = SUFFIX[ex]
    wcache, e10, sell = {}, {}, {}
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    cum = 0
    for line in p.stdout:
        if not line.endswith(b"\n"):
            break
        x = line.split(b"\t")
        cum += int(x[1])
        if x[0] != b"t" or len(x) < 11:
            continue
        s = x[4]
        w = wcache.get(s)
        if w is None:
            w = wcache[s] = s.endswith(suffix)
        if not w:
            continue
        a = e10.get(s)
        if a is None:
            a = e10[s] = array("q")
            sell[s] = array("b")
        a.append((cum // 1_000_000 - int(x[2])) // BUCKET_MS)
        sell[s].append(1 if x[10] == b"1" else 0)
    p.wait()
    res = {}
    for s, a in e10.items():
        b = np.frombuffer(a, "i8")
        u, inv = np.unique(b, return_inverse=True)
        res[s.decode()] = (u, np.bincount(inv).astype(np.int32), np.bincount(inv, weights=np.frombuffer(sell[s], "i1")).astype(np.int32))
    return ex, res


def main(out, par=3):
    jobs = []
    for ex in EX5:
        for d in sorted(glob.glob(f"{ROOTS5[ex]}/2026-*")):
            for f in sorted(glob.glob(f"{d}/trade*.tsv.zst")):
                if re.search(r"trade\d+-\d+\.tsv\.zst$", f):
                    jobs.append((ex, f))
    acc = {}
    t = time.time()
    with Pool(par, maxtasksperchild=10) as pool:
        for k, (ex, res) in enumerate(pool.imap_unordered(parse_file, jobs, chunksize=1)):
            for s, v in res.items():
                acc.setdefault((ex, s), []).append(v)
            if k % 20 == 0:
                print(k + 1, len(jobs), round(time.time() - t), flush=True)
    final = {}
    for key, lst in acc.items():
        b = np.concatenate([v[0] for v in lst])
        n = np.concatenate([v[1] for v in lst])
        sl = np.concatenate([v[2] for v in lst])
        u, inv = np.unique(b, return_inverse=True)
        final[key] = (u, np.bincount(inv, weights=n).astype(np.int32), np.bincount(inv, weights=sl).astype(np.int32))
    pickle.dump(final, open(out, "wb"), protocol=4)
    print({"keys": len(final), "s": round(time.time() - t)})


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 3)
