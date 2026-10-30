"""TK-461 шаг A: дамп ключей событий для режима M3 (пересечения по ключам, не по исходам; протокол P-TK461 §3).
Код детекторов не переписан — импорт one/coin из sm_s2, sm_s3 (A8, fl=1 — слом), sm_s5 (G5 × свип). Выход — три csv.gz + метка DONE.
Ключ события: S3/A8: t(мс бара t0), coin, tf, dir, k, fl; S2: coin, tf, dir, t, ev, pa, pb; S5: t, coin, dir, fl.
Запуск (calc): python -I tools/tk461_dump_keys.py <каталог l2> <каталог 1m csv.gz> <выход-каталог> [nx]"""
import sys, os, glob, importlib.util
from multiprocessing import Pool
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))


def _load(n):
    s = importlib.util.spec_from_file_location(n, os.path.join(HERE, n + ".py")); m = importlib.util.module_from_spec(s); sys.modules[n] = m; s.loader.exec_module(m); return m


S2 = _load("sm_s2"); S3 = _load("sm_s3"); S5 = _load("sm_s5")
S3_COLS = ["t", "coin", "tf", "dir", "k", "fl", "ful_f", "inv_f", "ful_c", "inv_c"]


if __name__ == "__main__":
    l2, src, out = sys.argv[1:4]; nx = int(sys.argv[4]) if len(sys.argv) > 4 else 15
    os.makedirs(out, exist_ok=True)
    with Pool(nx) as p:
        j3 = []
        for f in sorted(glob.glob(f"{l2}/l2-*-60-2.csv.gz")):
            sym = os.path.basename(f)[3:-7].split("-")[0]
            if os.path.exists(f"{src}/{sym}.csv.gz"): j3.append((f"{src}/{sym}.csv.gz", sym))
        D3 = pd.DataFrame([x for rr in p.map(S3.one, j3) for x in rr], columns=S3_COLS)
        D3[["t", "coin", "tf", "dir", "k", "fl"]].to_csv(f"{out}/keys-a8.csv.gz", index=False)
        fs = [(f, src) for tf in S2.TFS for f in sorted(glob.glob(f"{l2}/l2-*-{tf}-2.csv.gz"))]
        D2 = pd.concat(p.map(S2.one, fs), ignore_index=True)
        D2[["coin", "tf", "dir", "t", "ev", "pa", "pb"]].to_csv(f"{out}/keys-s2.csv.gz", index=False)
        j5 = [(f"{src}/{os.path.basename(f)[3:-7].split('-')[0]}.csv.gz", f) for f in sorted(glob.glob(f"{l2}/l2-*-60-2.csv.gz"))]
        j5 = [j for j in j5 if os.path.exists(j[0])]
        R = [x for rr, _ in p.map(S5.coin, j5) for x in rr]
        D5 = pd.DataFrame(R, columns=["t", "coin", "dir", "fl"] + [f"f{i}" for i in range(4)] + [f"c{i}" for i in range(4)])
        D5[["t", "coin", "dir", "fl"]].to_csv(f"{out}/keys-s5.csv.gz", index=False)
    print(len(D3), len(D2), len(D5))
    open(f"{out}/DONE_KEYS", "w").write(f"a8={len(D3)} s2={len(D2)} s5={len(D5)}\n")
