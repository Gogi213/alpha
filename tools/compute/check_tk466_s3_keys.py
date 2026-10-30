#!/usr/bin/env python3
"""TK-466: дифф ключей S3 — s-3.csv.gz против строк sm_s3.one на ТЕХ ЖЕ свечах (ключ: tt0, монета, ТФ, сторона, якорь).
python check_tk466_s3_keys.py <s-3.csv.gz> <каталог свечей> [nx] -> счёт: только в ref / только у нас; rc 1 при любом различии."""
import importlib.util
import os
import sys
from multiprocessing import Pool
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, ".."), "/data/tk0451"]
spec = importlib.util.spec_from_file_location("sm_s3", os.path.join(HERE, "..", "sm_s3.py"))
S3 = importlib.util.module_from_spec(spec)
spec.loader.exec_module(S3)
S, SRC = sys.argv[1], sys.argv[2].rstrip("/")


def job(sym):
    return S3.one((f"{SRC}/{sym}.csv.gz", sym))


if __name__ == "__main__":
    ours = pd.read_csv(S)
    nm = {v: k for k, v in S3.TFS.items()}
    syms = sorted(ours.sym.unique())
    with Pool(int(sys.argv[3]) if len(sys.argv) > 3 else 8) as p:
        rr = [x for r in p.map(job, syms) for x in r]
    ref = pd.DataFrame(rr, columns=["tt0", "sym", "tfn", "dir", "k", "fl", "a", "b", "c", "d"])
    ref["tf"] = ref.tfn.map(nm)
    ref["anchor"] = ref.k
    K = ["sym", "tf", "dir", "anchor", "tt0"]
    # ключи повторяются (разные свипы с одним t0) -> сверка мультимножеств: на ключ число строк и сумма a8
    g1 = ours.groupby(K).agg(n=("a8", "size"), f=("a8", "sum"))
    g2 = ref.groupby(K).agg(n=("fl", "size"), f=("fl", "sum"))
    m = g1.join(g2, how="outer", lsuffix="_o", rsuffix="_r")
    print("строк ref", len(ref), "наши", len(ours), "ключей ref", len(g2), "наши", len(g1))
    bad = m[(m.n_o != m.n_r) | (m.f_o != m.f_r)]
    print("ключей с расхождением (число строк или A8):", len(bad))
    print(bad.head(40).to_string())
    print("p459=1:", int(ours.p459.sum()), "из", len(ours))
    ro, lo = bad, bad
    sys.exit(0 if len(bad) == 0 else 1)
