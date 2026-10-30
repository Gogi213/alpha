"""TK-466: дифф ключей событий s-3 (tk461_s_events) с sm_s3.one (P-TK459). Запуск на calc, без аргументов."""
import sys, os, glob, importlib.util
sys.path.insert(0, "/data/tk459"); sys.path.insert(0, "/data/tk0451")
from multiprocessing import Pool
import pandas as pd

import sm_s3 as M
l2 = "/data/tk0451/l2"; src = "/srv/alpha-sda/candles-1m/binance-um"

if __name__ == "__main__":
    jobs = []
    for f in sorted(glob.glob(f"{l2}/l2-*-60-2.csv.gz")):
        sym = os.path.basename(f)[3:-7].split("-")[0]
        if os.path.exists(f"{src}/{sym}.csv.gz"):
            jobs.append((f"{src}/{sym}.csv.gz", sym))
    with Pool(12) as p:
        R = [x for rr in p.map(M.one, jobs) for x in rr]
    D = pd.DataFrame(R, columns=["t", "coin", "tf", "dir", "k", "fl", "a", "b", "c", "d"])[["t", "coin", "tf", "dir", "k", "fl"]]
    D["tf"] = D.tf.map({"1H": 60, "4H": 240, "1D": 1440})
    S = pd.read_csv("/data/tk0461/bpev/out/s-3.csv.gz")
    print("pool sm_s3", D.coin.nunique(), "ours", S.sym.nunique(), "only ours", sorted(set(S.sym) - set(D.coin)), "only theirs", sorted(set(D.coin) - set(S.sym)))
    S = S[S.p459 == 1]
    O = S[["tt0", "sym", "tf", "dir", "anchor", "a8"]].rename(columns={"tt0": "t", "sym": "coin", "anchor": "k", "a8": "fl"})
    key = ["t", "coin", "tf", "dir", "k", "fl"]
    print(len(D), len(O), "dup theirs", D.duplicated(key).sum(), "dup ours", O.duplicated(key).sum())
    m = O.merge(D, on=key, how="outer", indicator=True)
    x = m[m._merge != "both"].copy()
    x["utc"] = pd.to_datetime(x.t, unit="ms")
    print(x._merge.value_counts())
    print(x.sort_values(["tf", "dir", "coin"]).to_string())
