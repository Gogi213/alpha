#!/usr/bin/env python3
"""TK-512: перегон aux пар D1, у которых <пара>.aux.csv старого вида (в шапке нет srcOIA). Идемпотентно.
aux_sweep.py [--final]   сырьё mark/OI/ликвидаций CHFT-ног с бокса -> /root/tk512/sweep/raw; новый aux_feats по <пара>.features.csv; aux.csv/auxholes.csv пары заменяются на месте.
--final: ещё пересобрать <out>/auxholes.csv = объединение <пара>.auxholes.csv (только когда orch D1 не работает)."""
import csv, glob, os, subprocess, sys, shutil
P = "/root/p40core"; OUT = f"{P}/out_d1"; W = "/root/tk512/sweep"; AUX = "/root/p40core/aux_feats.py"
SSH = "ssh -c aes128-gcm@openssh.com -o Compression=no"
CHFT = {"binance", "okx", "bitget", "aster", "hyperliquid"}
pairs = {(r["asset"], r["pair"]): r for r in csv.DictReader(open(f"{P}/pairs_d1.csv"))}
def days(chunk):
    import datetime as dt
    s, k = chunk.split("+"); d0 = dt.date.fromisoformat(s)
    return [(d0 + dt.timedelta(i)).isoformat() for i in range(int(k))]
todo = []
for aux in sorted(glob.glob(f"{OUT}/*/*/*.aux.csv")):
    ch_dir, pair = os.path.dirname(aux), os.path.basename(aux)[:-8]
    if not os.path.exists(f"{ch_dir}/DONE"): continue
    if "srcOIA" in open(aux).readline(): continue
    asset = ch_dir.split("/")[-2]
    todo.append((asset, os.path.basename(ch_dir), pair, aux))
print("к перегону", len(todo), "пар", flush=True)
for asset, chunk, pair, aux in todo:
    r = pairs[(asset, pair)]; ds = days(chunk); roots = []
    for s in "AB":
        ex, sym = r["ex" + s], r["sym" + s]
        if ex not in CHFT: roots.append("-"); continue
        rs = []
        for d in ds:
            raw = f"{W}/raw/{asset}/{d}"; os.makedirs(raw, exist_ok=True)
            fl = f"{W}/files.{asset}.{d}.{s}"
            open(fl, "w").write("\n".join(f"chft/{ex}_futures/{d}/{h:02d}/{sym}_{k}.parquet" for h in range(24) for k in ("mark_price", "open_interest", "liquidations")) + "\n")
            rc = subprocess.call(["rsync", "-q", "-R", "--ignore-missing-args", f"--files-from={fl}", "-e", SSH, "box:alpha/stakan/", raw + "/"])
            if rc not in (0, 23, 24): sys.exit(f"rsync rc {rc} {asset} {d}")
            rs.append(f"{raw}/chft/{ex}_futures/{d}")
        roots.append(",".join(rs))
    base = f"{W}/res/{asset}.{chunk}.{pair}"; os.makedirs(os.path.dirname(base), exist_ok=True)
    shutil.copy(f"{os.path.dirname(aux)}/{pair}.features.csv", base + ".features.csv")
    rc = subprocess.call([sys.executable, AUX, base, roots[0], r["symA"], roots[1], r["symB"]])
    if rc: sys.exit(f"aux_feats rc {rc} {asset} {pair}")
    for ext in ("aux.csv", "auxholes.csv"):
        shutil.copy(f"{base}.{ext}", f"{os.path.dirname(aux)}/{pair}.{ext}.new"); os.replace(f"{os.path.dirname(aux)}/{pair}.{ext}.new", f"{os.path.dirname(aux)}/{pair}.{ext}")
    print("ok", asset, chunk, pair, flush=True)
if "--final" in sys.argv:
    seen, rows = set(), []
    for f in sorted(glob.glob(f"{OUT}/*/*/*.auxholes.csv")):
        for x in csv.reader(open(f)):
            if x and x[0] != "exchange" and tuple(x) not in seen: seen.add(tuple(x)); rows.append(x)
    with open(f"{OUT}/auxholes.csv", "w", newline="") as f:
        w = csv.writer(f); w.writerow(["exchange", "contract", "date", "hour", "kind"]); w.writerows(rows)
    print("auxholes.csv пересобран:", len(rows), "строк")
