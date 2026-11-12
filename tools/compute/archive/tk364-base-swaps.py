"""TK-364: свопы пулов Base через публичные RPC (eth_getLogs окнами), по суткам UTC, с возобновлением.
usage: tk364-base-swaps.py <outdir> <YYYY-MM-DD нач> <YYYY-MM-DD кон, искл.> [--threads-drpc N] [--maxblocks M]
Пулы — POOLS ниже (адреса полные, источник — DexScreener 23.10). Логи пула берутся без фильтра topic
(окно ≤100 блоков), оставляются Swap-события: Uniswap V2 / Aerodrome V2 / V3-CL (Slipstream).
Выход: <outdir>/<SYM>/<день>.csv (block,ts,logidx,tx,topic0,data) + <день>.done; итог — <outdir>/DONE.json.
Время блока: линейно по 2 с, если границы суток это подтверждают; иначе eth_getBlockByNumber на блок со свопом."""
import sys, os, json, time, threading, queue, urllib.request, datetime as dt

POOLS = {
    "AERO": ("0x6cDcb1C4A4D1C3C6d054b27AC5B77e89eAFb971d", "aerodrome"),
    "VVV": ("0x01784ef301D79e4B2DF3a21ad9a536d4cF09A5Ce", "aerodrome"),
    "FLOCK": ("0xd7338B6F338DA54b922bbBC6fBE3EFEDD5Ce35fa", "aerodrome"),
}
SWAP_TOPICS = {
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822": "univ2",
    "0xb3e2773606abfd36b5bd91394b3a54d1398336c65005baf7bf7a05efeffaf75b": "aerov2",
    "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67": "v3",
}
ENDPOINTS = [("https://base.drpc.org", 100)]  # 1rpc: история обрезана (earliest ≈ 25.09), не годен

out, D0, D1 = sys.argv[1:4]
args = sys.argv[4:]
THR_DRPC = int(args[args.index("--threads-drpc") + 1]) if "--threads-drpc" in args else 1
MAXB = int(args[args.index("--maxblocks") + 1]) if "--maxblocks" in args else 0
CALLS = [0]; ERRS = [0]

def rpc(url, method, params, tries=8):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    err = None
    for i in range(tries):
        CALLS[0] += 1
        try:
            req = urllib.request.Request(url, body, {"content-type": "application/json", "user-agent": "Mozilla/5.0"})
            r = json.load(urllib.request.urlopen(req, timeout=40))
            if "error" in r:
                raise RuntimeError(r["error"])
            return r["result"]
        except Exception as e:
            err = e; ERRS[0] += 1
            time.sleep(min(30, 1.5 * (i + 1)))
    raise err

def blk_ts(url, n):
    return int(rpc(url, "eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)

def find_block(url, ts):
    lo, hi = 1, int(rpc(url, "eth_blockNumber", []), 16)
    while lo < hi:
        mid = (lo + hi) // 2
        if blk_ts(url, mid) < ts: lo = mid + 1
        else: hi = mid
    return lo

def day_ts(d):
    return int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())

def do_task(url, win, sym, day):
    addr = POOLS[sym][0]
    d0 = day_ts(day)
    b0, b1 = find_block(url, d0), find_block(url, d0 + 86400)
    if MAXB: b1 = min(b1, b0 + MAXB)
    t0, t1 = blk_ts(url, b0), blk_ts(url, b1 - 1)
    linear = (t1 - t0) == 2 * (b1 - 1 - b0)
    rows, cache = [], {}
    b = b0
    while b < b1:
        e = min(b + win, b1) - 1
        logs = rpc(url, "eth_getLogs", [{"address": addr, "fromBlock": hex(b), "toBlock": hex(e)}])
        for l in logs:
            t = l["topics"][0].lower() if l["topics"] else ""
            if t not in SWAP_TOPICS: continue
            bn = int(l["blockNumber"], 16)
            if linear: ts = t0 + 2 * (bn - b0)
            else:
                if bn not in cache: cache[bn] = blk_ts(url, bn)
                ts = cache[bn]
            rows.append(f"{bn},{ts},{int(l['logIndex'], 16)},{l['transactionHash']},{SWAP_TOPICS[t]},{l['data']}")
        b = e + 1
    p = os.path.join(out, sym); os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, day + ".csv.tmp"), "w", newline="\n") as f:
        f.write("block,ts,logidx,tx,kind,data\n"); f.write("\n".join(rows) + ("\n" if rows else ""))
    os.replace(os.path.join(p, day + ".csv.tmp"), os.path.join(p, day + ".csv"))
    open(os.path.join(p, day + ".done"), "w").write(f"{len(rows)} swaps blocks {b0}-{b1} linear={linear}\n")
    return len(rows)

days = []
d = dt.date.fromisoformat(D0)
while d < dt.date.fromisoformat(D1):
    days.append(d.isoformat()); d += dt.timedelta(days=1)
q = queue.Queue()
for day in days:
    for sym in POOLS:
        if not os.path.exists(os.path.join(out, sym, day + ".done")):
            q.put((sym, day))
total = q.qsize(); done = [0]; t_start = time.time(); lock = threading.Lock()

def worker(url, win):
    while True:
        try: sym, day = q.get_nowait()
        except queue.Empty: return
        try:
            n = do_task(url, win, sym, day)
        except Exception as e:
            print("FAIL", sym, day, url, e, flush=True); continue
        with lock:
            done[0] += 1
            print(f"{time.strftime('%H:%M:%S')} {sym} {day} swaps={n} {done[0]}/{total} calls={CALLS[0]} err={ERRS[0]}", flush=True)

ths = []
for url, win in ENDPOINTS:
    for _ in range(THR_DRPC if "drpc" in url else 1):
        t = threading.Thread(target=worker, args=(url, win)); t.start(); ths.append(t)
for t in ths: t.join()
missing = [(s, dd) for dd in days for s in POOLS if not os.path.exists(os.path.join(out, s, dd + ".done"))]
json.dump({"tasks": total, "missing": missing, "calls": CALLS[0], "errors": ERRS[0], "wall_s": round(time.time() - t_start)},
          open(os.path.join(out, "DONE.json"), "w"))
print("DONE missing", len(missing), flush=True)
