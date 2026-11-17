"""Проверяющий arb-tokyo: перечитать сырые книги Bybit (orderbook.1) для выбранных монет с обоими временами: ts (как в отчёте, E) и cts (время движка).
Пишет <out>/bts/parsed/bybit/*.npz (e = ts) и <out>/bcts/parsed/bybit/*.npz (e = cts), прочие биржи — жёсткие ссылки из /data/arb-tokyo/full/b/parsed.
Сводка распределений ts − cts и шага ts между записями — <out>/bybit_ts.json. Запуск: python3 bybit_cts.py <out> COIN,COIN,...
"""
import glob, json, os, subprocess, sys, time
from array import array
from multiprocessing import Pool
from pathlib import Path
import numpy as np
sys.path.insert(0, str(Path(__file__).parent))
from arb_live import symmap_for
from common import LATE_MS

SRC = "/data/turbo-probe/soak-bybit/data"
FULL = "/data/arb-tokyo/full/b/parsed"
AWK = r'''BEGIN{while((getline s < SF)>0) S[s]=1} {c+=$2} $1=="b" && ($5 in S){printf "%s\t%.0f\t%s\t%s\t%s\t%s\t%s\t%s\n",$5,c,$3,$4,$7,$8,$9,$10}'''


def one(args):
    f, sf, out = args
    day = Path(f).parent.name
    stem = Path(f).name.split(".")[0]
    tag = f"{day}-{stem}.1"
    p = subprocess.Popen(f"zstd -dc {f} | mawk -F'\t' -v SF={sf} '{AWK}'", shell=True, stdout=subprocess.PIPE, bufsize=1 << 22)
    arrs, last = {}, {}
    for line in p.stdout:
        x = line.rstrip(b"\n").split(b"\t")
        s, rx, lag, d = x[0], int(x[1]) // 1_000_000, int(x[2]), int(x[3])
        k = (x[4], x[6])
        if lag <= LATE_MS and last.get(s) == k:
            continue
        last[s] = k
        a = arrs.get(s)
        if a is None:
            a = arrs[s] = tuple(array(c) for c in "qqiidddd")
        a[0].append(rx - lag); a[1].append(rx); a[2].append(lag); a[3].append(d)
        a[4].append(float(x[4])); a[5].append(float(x[6])); a[6].append(float(x[5])); a[7].append(float(x[7]))
    p.wait()
    stats = []
    for s, a in arrs.items():
        e, rx, lag, d = (np.frombuffer(a[i], t) for i, t in ((0, "i8"), (1, "i8"), (2, "i4"), (3, "i4")))
        bid, ask, bsz, asz = (np.frombuffer(a[i], "f8") for i in (4, 5, 6, 7))
        sym = s.decode()
        for kind, ee in (("bts", e), ("bcts", e - d)):
            od = Path(out, kind, "parsed", "bybit"); od.mkdir(parents=True, exist_ok=True)
            np.savez(od / f"{sym}.{tag}.npz", e=ee, rx=rx, lag=lag, bid=bid, ask=ask, bsz=bsz, asz=asz)
        np.save(Path(out, "dd", f"{sym}.{tag}.npy"), np.c_[e, d])
        stats.append((sym, len(e)))
    return f, stats


def main(out, coins):
    out = Path(out)
    (out / "dd").mkdir(parents=True, exist_ok=True)
    symmap, _ = symmap_for("2026-11-16")
    want = sorted(s for s, (c, m) in symmap["bybit"].items() if c in set(coins))
    sf = out / "syms.txt"
    sf.write_text("\n".join(want) + "\n")
    files = sorted(f for d in ("2026-11-12", "2026-11-13", "2026-11-14", "2026-11-15", "2026-11-16") for f in glob.glob(f"{SRC}/{d}/book*.tsv.zst"))
    t = time.time()
    with Pool(4) as p:
        for k, (f, st) in enumerate(p.imap_unordered(one, [(f, sf, str(out)) for f in files])):
            print(k, len(files), Path(f).name, sum(n for _, n in st), round(time.time() - t), flush=True)
    for kind in ("bts", "bcts"):
        for ex in ("binance", "okx", "bingx"):
            d = out / kind / "parsed" / ex
            d.mkdir(parents=True, exist_ok=True)
            subprocess.run(["cp", "-al", "--no-clobber", f"{FULL}/{ex}/.", str(d) + "/"], check=True)
    print("syms", want, "s", round(time.time() - t))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2].split(","))
