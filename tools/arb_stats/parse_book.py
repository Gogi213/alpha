"""Этап A: читает файлы book<N> одной биржи за последний запуск суток, пишет по символу изменения лучшей цены.

Пишет <out>/<биржа>/<символ>.npz: e (время события биржи E, мс), rx (приём, мс), lag (мс), bid, ask, bsz, asz.
Запись пишется, только если (bid, ask) изменилась относительно предыдущей по приёму (или лаг > 2 с: такие записи
BingX приходят вне порядка и не снимаются как повтор). Размер/число записей других типов не читаются.
Запуск: python3 parse_book.py <биржа> <день> <end_rx_ms> <out> <метка шарда> <файл>...
"""
import json
import subprocess
import sys
from array import array
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parent))
from common import LATE_MS, build_symmap  # noqa: E402


def run(ex, day, end_ms, out, tag, files):
    symmap = build_symmap(day)[ex]
    want = {s.encode(): s for s in symmap}
    arrs, last = {}, {}
    nrows = nb = nkept = 0
    for f in files:
        p = subprocess.Popen(["zstd", "-dc", f], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, bufsize=1 << 22)
        cum = 0
        for line in p.stdout:
            x = line.split(b"\t")
            nrows += 1
            cum += int(x[1])
            if x[0] != b"b":
                continue
            nb += 1
            rx = cum // 1_000_000
            if rx > end_ms:
                p.kill()
                break
            s = x[4]
            if s not in want:
                continue
            k = (x[6], x[8])
            lag = int(x[2])
            if lag <= LATE_MS and last.get(s) == k:
                continue
            last[s] = k
            a = arrs.get(s)
            if a is None:
                a = arrs[s] = (array("q"), array("q"), array("i"), array("d"), array("d"), array("d"), array("d"))
            a[0].append(rx - lag)
            a[1].append(rx)
            a[2].append(lag)
            a[3].append(float(k[0]))
            a[4].append(float(k[1]))
            a[5].append(float(x[7]))
            a[6].append(float(x[9]))
            nkept += 1
        p.wait()
    od = Path(out, ex)
    od.mkdir(parents=True, exist_ok=True)
    for s, a in arrs.items():
        np.savez(od / (want[s] + "." + tag + ".npz"), e=np.frombuffer(a[0], "i8"), rx=np.frombuffer(a[1], "i8"), lag=np.frombuffer(a[2], "i4"),
                 bid=np.frombuffer(a[3], "f8"), ask=np.frombuffer(a[4], "f8"), bsz=np.frombuffer(a[5], "f8"), asz=np.frombuffer(a[6], "f8"))
    print(json.dumps({"ex": ex, "files": [Path(f).name for f in files], "rows": nrows, "book_rows": nb, "kept": nkept, "symbols": len(arrs)}), flush=True)


if __name__ == "__main__":
    run(sys.argv[1], sys.argv[2], int(sys.argv[3]), sys.argv[4], sys.argv[5], sys.argv[6:])
