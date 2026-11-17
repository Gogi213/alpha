"""П-42 §13.9: файлы вариантов тиковой сетки — `decorate <этап>` дописывает `lat=4 latx=4` в <ROOT>/variants-<этап>.txt (и убирает справочные slip*/best из final).

  P42_ROOT=data/p42v2-t544 python tools/p42/tgrid.py decorate stage1|ext|joint|final
"""
import os
import sys

ROOT = os.environ["P42_ROOT"]


def main():
    st = sys.argv[2]
    p = os.path.join(ROOT, "variants-%s.txt" % st)
    out = []
    for ln in open(p, encoding="utf-8"):
        ln = ln.strip()
        if not ln:
            continue
        vid = ln.split()[0]
        if "_slip" in vid or vid.endswith("_best"):
            continue
        out.append(ln + " lat=4 latx=4")
    open(p, "w", encoding="utf-8", newline="\n").write("\n".join(out) + "\n")
    print("variants-%s: %d" % (st, len(out)))


if __name__ == "__main__":
    main()
