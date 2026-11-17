"""Формат записей book по запускам: число полей строки 'b', пример, типы полей, события 'c' (connected/subscribed/…) из первых 4 МБ распаковки каждого файла book00.

Запуск на srv: python3 inventory_format.py [метка_запуска_от=1794527607]. Печатает по биржам и запускам: поля 'b' (число, пример без цен) и сводку событий c.
"""
import collections
import glob
import os
import re
import subprocess
import sys

sys.path.insert(0, os.path.dirname(__file__))
from common import ROOTS  # noqa: E402


def head(path, n=4_000_000):
    p = subprocess.Popen(["zstd", "-dc", path], stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    data = p.stdout.read(n)
    p.kill()
    return data


def main(min_run=1794483446):
    for ex, root in ROOTS.items():
        files = {}
        for f in glob.glob(f"{root}/2026-11-1[2345]/book00-*.tsv.zst"):
            rid = int(re.search(r"-(\d+)\.tsv\.zst$", f).group(1))
            if rid >= min_run:
                files.setdefault(rid, f)
        print("==", ex)
        for rid in sorted(files):
            d = head(files[rid])
            nf, kinds, ex_line = collections.Counter(), collections.Counter(), None
            for line in d.split(b"\n")[:-1]:
                x = line.split(b"\t")
                kinds[x[0]] += 1
                if x[0] == b"b":
                    nf[len(x)] += 1
                    ex_line = ex_line or line
                elif x[0] == b"c":
                    kinds[b"c:" + x[2]] += 1
            print(rid, "поля b:", dict(nf), "виды:", {k.decode(): v for k, v in kinds.items()}, "|", (ex_line or b"")[:120].decode(errors="replace").replace("\t", " "))


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 1794483446)
