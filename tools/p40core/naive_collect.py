#!/usr/bin/env python3
"""TK-522 (В-125…В-127): сборка `<база>[.k2.k2|.k1p60].naive.csv` выхода `orch.py --naive-only` в один файл (csv.gz).
Запуск: naive_collect.py <out_orch> <выход.csv.gz>. Строки: ctl (real | K2 | K1+60000), asset, chunk, pair + колонки write_naive (ядро, main.rs); `<база>.k2.naive.csv` (настоящие эпизоды прогона варианта K2) пропускается как дубль real.
Ключ к `<база>` и `<база>.path.csv` — (pair, theta, t0_ms); возвращает число строк по ctl."""
import collections, gzip, os, sys

CTL = (("k2.k2", "K2"), ("k1p60", "K1+60000"), ("k2", None), ("drain", None))


def main(out, dst):
    n = collections.Counter()
    head = None
    with gzip.open(dst, "wt", newline="") as w:
        for root, _, fs in sorted(os.walk(out)):
            for f in sorted(fs):
                if not f.endswith(".naive.csv"):
                    continue
                stem = f[: -len(".naive.csv")]
                pair, _, var = stem.partition(".")
                ctl = "real"
                if var:
                    hit = [c for v, c in CTL if var == v]
                    if not hit or hit[0] is None:
                        continue
                    ctl = hit[0]
                rel = os.path.relpath(root, out).replace(os.sep, "/").split("/")
                asset, chunk = (rel + ["", ""])[:2]
                with open(os.path.join(root, f), encoding="utf-8") as r:
                    h = r.readline().rstrip("\n")
                    if head is None:
                        head = h
                        w.write(f"ctl,asset,chunk,pair,{h}\n")
                    elif h != head:
                        sys.exit(f"{root}/{f}: шапка иная")
                    for line in r:
                        w.write(f"{ctl},{asset},{chunk},{pair},{line}")
                        n[ctl] += 1
    print(dict(n))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        sys.exit(__doc__)
    main(sys.argv[1], sys.argv[2])
