"""П-40 G0 (TK-496 возврат п.1,3): полный проход Binance CHFT — те же поля, что g0_fast.one, по всем файлам; возобновляемый.
Запуск на 5.231.25.224: nice -n 19 python3 g0_binance_full.py <out.jsonl> [процессов]. Исходов не касается."""
import os, sys, json, glob
from multiprocessing import Pool
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import g0_fast

ROOT = "/data/stakan/chft/binance_futures"

def work(p):
    r = g0_fast.one(p)
    h = p.split("/")
    r["ex"] = "binance_futures"; r["unit"] = ["binance_futures", h[-3], h[-2]]
    return r

if __name__ == "__main__":
    out = sys.argv[1]; nproc = int(sys.argv[2]) if len(sys.argv) > 2 else 6
    done = set()
    if os.path.exists(out):
        for l in open(out):
            try: d = json.loads(l); done.add((d["unit"][1], d["unit"][2], d["file"]))
            except Exception: pass
    files = sorted(glob.glob(ROOT + "/*/*/*_orderbook.parquet"))
    todo = [p for p in files if (p.split("/")[-3], p.split("/")[-2], os.path.basename(p)) not in done]
    print(len(files), "файлов,", len(todo), "осталось", flush=True)
    with Pool(nproc) as pool, open(out, "a") as f:
        for i, r in enumerate(pool.imap_unordered(work, todo, chunksize=4)):
            f.write(json.dumps(r) + "\n")
            if i % 2000 == 0: f.flush(); print(i, flush=True)
    open(out + ".done", "w").write("ok")
