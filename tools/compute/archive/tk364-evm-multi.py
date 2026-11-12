"""TK-364: докачка свопов EVM без ключа — публичный RPC (drpc), eth_getLogs по МАССИВУ пулов окнами ≤100 блоков.
usage: tk364-evm-multi.py <outdir> <chain: bsc|base|ethereum> <pools.json> <url> <дни через запятую, в порядке сгорания> [--skip-dir D] [--threads N] [--batch 60]
Пропуск пул-суток, у которых есть <skip-dir>/<chain>/<SYM>/<день>.done. Выход: <outdir>/<chain>/<SYM>/<день>.csv
(block,ts,logidx,tx,kind,data,txidx,t1,t2 — t1/t2 = topics sender/recipient) + .done; DONE.json в конце. Окно 100 блоков, бэкофф на 429."""
import sys, os, json, time, threading, queue, urllib.request, datetime as dt
KIND = {"0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822": "univ2",
        "0xb3e2773606abfd36b5bd91394b3a54d1398336c65005baf7bf7a05efeffaf75b": "aerov2",
        "0xc42079f94a6350d7e6235f29174924f928cc2ac818eb64fed8004e115fbcca67": "v3",
        "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83": "pcs-v3"}
out, CHAIN, POOLFILE, URL, DAYS = sys.argv[1:6]
a = sys.argv[6:]
SKIP = a[a.index("--skip-dir") + 1] if "--skip-dir" in a else None
THR = int(a[a.index("--threads") + 1]) if "--threads" in a else 2
BATCH = int(a[a.index("--batch") + 1]) if "--batch" in a else 60
WIN = 100
POOLS = {v["sym"]: v["pool"].lower() for v in json.load(open(POOLFILE)).values() if v["chain"] == CHAIN and len(v["pool"]) == 42}
BY_ADDR = {p: s for s, p in POOLS.items()}
CALLS = [0]; ERRS = [0]; LOCK = threading.Lock()

def rpc(method, params, tries=12):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    err = None
    for i in range(tries):
        CALLS[0] += 1
        try:
            req = urllib.request.Request(URL, body, {"content-type": "application/json", "user-agent": "Mozilla/5.0"})
            r = json.load(urllib.request.urlopen(req, timeout=40))
            if "error" in r: raise RuntimeError(str(r["error"])[:150])
            return r["result"]
        except Exception as e:
            err = e; ERRS[0] += 1
            time.sleep(min(60, 2 ** min(i, 6) * 0.5))
    raise err

def blk_ts(n): return int(rpc("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)
def find_block(ts):
    lo, hi = (30_000_000 if CHAIN == "base" else 1_000_000), int(rpc("eth_blockNumber", []), 16)
    while lo < hi:
        mid = (lo + hi) // 2
        if blk_ts(mid) < ts: lo = mid + 1
        else: hi = mid
    return lo
def day_ts(d): return int(dt.datetime.strptime(d, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
BND = {}
def bnd(ts):
    with LOCK:
        if ts in BND: return BND[ts]
    b = find_block(ts)
    with LOCK: BND[ts] = b
    return b

def done_p(root, s, d): return os.path.exists(os.path.join(root, CHAIN, s, d + ".done"))
def do_task(day, syms):
    d0 = day_ts(day); b0, b1 = bnd(d0), bnd(d0 + 86400)
    addrs = [POOLS[s] for s in syms]; rows = {s: [] for s in syms}
    b = b0
    while b < b1:
        e = min(b + WIN, b1) - 1
        for l in rpc("eth_getLogs", [{"address": addrs, "fromBlock": hex(b), "toBlock": hex(e)}]):
            k = KIND.get(l["topics"][0].lower() if l["topics"] else "")
            if not k: continue
            t = l["topics"]
            rows[BY_ADDR[l["address"].lower()]].append(",".join([str(int(l["blockNumber"], 16)), str(int(l["blockTimestamp"], 16)),
                str(int(l["logIndex"], 16)), l["transactionHash"], k, l["data"], str(int(l["transactionIndex"], 16)),
                t[1][-40:] if len(t) > 1 else "", t[2][-40:] if len(t) > 2 else ""]))
        b = e + 1
        time.sleep(0.1)
    for s in syms:
        p = os.path.join(out, CHAIN, s); os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, day + ".csv.tmp"), "w", newline="\n") as f:
            f.write("block,ts,logidx,tx,kind,data,txidx,t1,t2\n"); f.write("\n".join(rows[s]) + ("\n" if rows[s] else ""))
        os.replace(os.path.join(p, day + ".csv.tmp"), os.path.join(p, day + ".csv"))
        open(os.path.join(p, day + ".done"), "w").write(f"{len(rows[s])} swaps blocks {b0}-{b1} ts=blockTimestamp(s) src=drpc-multi\n")
    return sum(len(v) for v in rows.values())

q = queue.Queue()
for day in DAYS.split(","):
    miss = [s for s in POOLS if not done_p(out, s, day) and not (SKIP and done_p(SKIP, s, day))]
    for i in range(0, len(miss), BATCH): q.put((day, miss[i:i + BATCH]))
total = q.qsize(); n_done = [0]; fails = []; t_start = time.time()
def worker():
    while True:
        try: day, syms = q.get_nowait()
        except queue.Empty: return
        try: n = do_task(day, syms)
        except Exception as e:
            fails.append((day, syms)); print("FAIL", day, len(syms), str(e)[:150], flush=True); continue
        with LOCK: n_done[0] += 1
        print(f"{time.strftime('%H:%M:%S')} {CHAIN} {day} pools={len(syms)} swaps={n} {n_done[0]}/{total} calls={CALLS[0]} err={ERRS[0]}", flush=True)
ths = [threading.Thread(target=worker) for _ in range(THR)]
[t.start() for t in ths]; [t.join() for t in ths]
json.dump({"tasks": total, "failed": fails, "calls": CALLS[0], "errors": ERRS[0], "wall_s": round(time.time() - t_start)}, open(os.path.join(out, CHAIN + "-DONE.json"), "w"))
print("DONE failed", len(fails), flush=True)
