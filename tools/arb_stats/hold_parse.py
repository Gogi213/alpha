"""Разбор книг arb-holding v4 из сырья ВСЕХ запусков и ВСЕХ USDT-символов пяти бирж (без отбора по пулам дня и без MINRUN).

Две вещи за один проход по файлу `book*.tsv.zst`:
  1. куски цен — те же поля и то же снятие повторов, что в parse_inc (`<символ>.<день>-<файл>.1.npz`, сжатые);
  2. пропуски между ВСЕМИ записями `b` символа (в том числе сменами только размера): число записей, первое и последнее E и пары (пред., след.) E с промежутком > GAP_MS —
     по ним hold_rawgaps.py строит интервалы «молчания ноги» без повторного чтения сырья (фильтр stale, прочтение «сырьё»).
Запуск: python3 hold_parse.py <выход> [PAR]; выход: <выход>/parsed/<биржа>/*.npz, <выход>/gaps/<биржа>-<метка>.pkl, <выход>/done/<биржа>-<метка> (повторный запуск пропускает готовые файлы).
Недописанная последняя строка живого файла не читается (как в parse_inc). Время E = приём − lag, приём = накопленная сумма dns по ВСЕМ строкам файла.
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
from common import LATE_MS  # noqa: E402
from hold_common import EX5, ROOTS5  # noqa: E402

GAP_MS = 5_000                         # хранятся только промежутки > 5 с (порог «нога устарела» — слово владельца)
SUFFIX = {"binance": b"USDT", "bybit": b"USDT", "okx": b"-USDT-SWAP", "bingx": b"-USDT", "gate": b"_USDT"}
G = {}


def tag_of(day, path):
    return f"{day}-{Path(path).name.removesuffix('.tsv.zst')}"


def parse_file(job):
    ex, day, path = job
    tag = tag_of(day, path)
    done = Path(G["out"], "done", f"{ex}-{tag}")
    if done.exists():
        return ex, 0
    suffix = SUFFIX[ex]
    wcache = {}
    last, arrs, alle = {}, {}, {}
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
    cum, kept = 0, 0
    for line in p.stdout:
        if not line.endswith(b"\n"):          # недописанная строка живого файла
            break
        x = line.split(b"\t")
        cum += int(x[1])
        if x[0] != b"b" or len(x) < 10:
            continue
        s = x[4]
        w = wcache.get(s)
        if w is None:
            w = wcache[s] = s.endswith(suffix)
        if not w:
            continue
        lag = int(x[2])
        rx = cum // 1_000_000
        e = rx - lag
        ae = alle.get(s)
        if ae is None:
            ae = alle[s] = array("q")
        ae.append(e)
        k = (x[6], x[8])
        if lag <= LATE_MS and last.get(s) == k:
            continue
        last[s] = k
        a = arrs.get(s)
        if a is None:
            a = arrs[s] = (array("q"), array("q"), array("i"), array("d"), array("d"), array("d"), array("d"))
        a[0].append(e)
        a[1].append(rx)
        a[2].append(lag)
        a[3].append(float(k[0]))
        a[4].append(float(k[1]))
        a[5].append(float(x[7]))
        a[6].append(float(x[9]))
        kept += 1
    p.wait()
    od = Path(G["out"], "parsed", ex)
    od.mkdir(parents=True, exist_ok=True)
    for s, a in arrs.items():
        np.savez_compressed(od / f"{s.decode()}.{tag}.1.npz", e=np.frombuffer(a[0], "i8"), rx=np.frombuffer(a[1], "i8"), lag=np.frombuffer(a[2], "i4"),
                            bid=np.frombuffer(a[3], "f8"), ask=np.frombuffer(a[4], "f8"), bsz=np.frombuffer(a[5], "f8"), asz=np.frombuffer(a[6], "f8"))
    gaps = {}
    for s, ae in alle.items():
        e = np.sort(np.frombuffer(ae, "i8"))
        d = np.diff(e)
        i = np.flatnonzero(d > GAP_MS)
        gaps[s.decode()] = (int(len(e)), int(e[0]), int(e[-1]), e[:-1][i].copy(), e[1:][i].copy())
    gp = Path(G["out"], "gaps", f"{ex}-{tag}.pkl")
    tmp = gp.with_suffix(".tmp")
    pickle.dump(gaps, open(tmp, "wb"), protocol=4)
    tmp.rename(gp)
    done.write_text(f"{kept}\n")
    return ex, kept


def main(out, par=4):
    for sub in ("parsed", "gaps", "done"):
        Path(out, sub).mkdir(parents=True, exist_ok=True)
    G["out"] = out
    jobs = []
    for ex in EX5:
        for d in sorted(glob.glob(f"{ROOTS5[ex]}/2026-*")):
            for f in sorted(glob.glob(f"{d}/book*.tsv.zst")):
                if re.search(r"book\d+-\d+\.tsv\.zst$", f):
                    jobs.append((ex, Path(d).name, f))
    jobs.sort(key=lambda j: -os.path.getsize(j[2]))
    t = time.time()
    print("files", len(jobs), flush=True)
    with Pool(par, maxtasksperchild=8) as pool:
        for k, (ex, kept) in enumerate(pool.imap_unordered(parse_file, jobs, chunksize=1)):
            if k % 10 == 0:
                print(k + 1, len(jobs), ex, kept, round(time.time() - t), flush=True)
    print("DONE", round(time.time() - t), flush=True)


if __name__ == "__main__":
    main(sys.argv[1], int(sys.argv[2]) if len(sys.argv) > 2 else 4)
