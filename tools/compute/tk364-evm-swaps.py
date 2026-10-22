"""TK-364: свопы пулов EVM через NodeReal (ключ — env NODEREAL_BSC / NODEREAL_BASE, не печатать), eth_getLogs окнами по суткам UTC, с возобновлением.
usage: tk364-evm-swaps.py <outdir> <день нач> <день кон, искл.> <chain: ethereum|bsc|base> <pools.json> [--threads N] [--win W] [--only SYM,SYM]
pools.json — выход tk364-pools2.py (ключи SYM@chain). Время = blockTimestamp лога (с). Окно делится пополам при отказе по размеру.
Выход: <outdir>/<SYM>/<день>.csv (block,ts,logidx,tx,kind,data) + .done; итог — <outdir>/DONE.json."""
import sys, os, json, time, threading, queue, urllib.request, datetime as dt

SWAP_TOPICS = {
    "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822": "univ2",
    "0xb3e2773606abfd36b5bd91394b3a54d1398336c65005baf7bf7a05efeffaf75b": "aerov2",
    "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67": "v3",
    "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83": "pcs-v3",
}
out, D0, D1, CHAIN, POOLFILE = sys.argv[1:6]
args = sys.argv[6:]
THR = int(args[args.index("--threads") + 1]) if "--threads" in args else 2
WIN = int(args[args.index("--win") + 1]) if "--win" in args else 2000
ONLY = args[args.index("--only") + 1].split(",") if "--only" in args else None
_bsc = os.environ["NODEREAL_BSC"]
_key = _bsc.rstrip("/").split("/")[-1]
URL = {"bsc": _bsc, "ethereum": f"https://eth-mainnet.nodereal.io/v1/{_key}",
       "base": os.environ.get("NODEREAL_BASE", "")}[CHAIN]
POOLS = {v["sym"]: (v["pool"], v["dex"]) for k, v in json.load(open(POOLFILE)).items()
         if v["chain"] == CHAIN and not (len(v["pool"]) > 42) and (ONLY is None or v["sym"] in ONLY)}
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
    lo, hi = (50_000_000 if CHAIN == "base" else 1), int(rpc(url, "eth_blockNumber", []), 16)
    while lo < hi:
        mid = (lo + hi) // 2
        if blk_ts(url, mid) < ts: lo = mid + 1
        else: hi = mid
    return lo

def day_ts(d):
    return int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())

BND = {}
def day_block(url, ts):
    if ts not in BND: BND[ts] = find_block(url, ts)
    return BND[ts]

def get_logs(url, addr, b, e, depth=0):
    try:
        return rpc(url, "eth_getLogs", [{"address": addr, "fromBlock": hex(b), "toBlock": hex(e)}], tries=3)
    except Exception as ex:
        if e > b and depth < 12:
            m = (b + e) // 2
            return get_logs(url, addr, b, m, depth + 1) + get_logs(url, addr, m + 1, e, depth + 1)
        raise

def do_task(url, win, sym, day):
    addr = POOLS[sym][0]
    d0 = day_ts(day)
    b0, b1 = day_block(url, d0), day_block(url, d0 + 86400)
    rows = []
    b = b0
    while b < b1:
        e = min(b + win, b1) - 1
        for l in get_logs(url, addr, b, e):
            t = l["topics"][0].lower() if l["topics"] else ""
            if t not in SWAP_TOPICS: continue
            rows.append(f"{int(l['blockNumber'], 16)},{int(l['blockTimestamp'], 16)},{int(l['logIndex'], 16)},{l['transactionHash']},{SWAP_TOPICS[t]},{l['data']}")
        b = e + 1
    p = os.path.join(out, sym); os.makedirs(p, exist_ok=True)
    with open(os.path.join(p, day + ".csv.tmp"), "w", newline="\n") as f:
        f.write("block,ts,logidx,tx,kind,data\n"); f.write("\n".join(rows) + ("\n" if rows else ""))
    os.replace(os.path.join(p, day + ".csv.tmp"), os.path.join(p, day + ".csv"))
    open(os.path.join(p, day + ".done"), "w").write(f"{len(rows)} swaps blocks {b0}-{b1} ts=blockTimestamp(s)\n")
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
            print("FAIL", sym, day, str(e)[:200], flush=True); continue
        with lock:
            done[0] += 1
            print(f"{time.strftime('%H:%M:%S')} {sym} {day} swaps={n} {done[0]}/{total} calls={CALLS[0]} err={ERRS[0]}", flush=True)

ths = []
for _ in range(THR):
    t = threading.Thread(target=worker, args=(URL, WIN)); t.start(); ths.append(t)
for t in ths: t.join()
missing = [(s, dd) for dd in days for s in POOLS if not os.path.exists(os.path.join(out, s, dd + ".done"))]
json.dump({"tasks": total, "missing": missing, "calls": CALLS[0], "errors": ERRS[0], "wall_s": round(time.time() - t_start)},
          open(os.path.join(out, "DONE.json"), "w"))
print("DONE missing", len(missing), flush=True)
