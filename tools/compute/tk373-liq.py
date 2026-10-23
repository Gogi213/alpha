"""TK-373: Mint/Burn (ликвидность) пулов EVM — публичный RPC, eth_getLogs по МАССИВУ пулов окнами ≤100 блоков, фильтр topic0 = Mint/Burn (v2 и v3).
usage: tk373-liq.py <outdir> <chain: bsc|base|ethereum> <pools.json> <url> <дни через запятую> [--only-dirs F] [--threads N] [--batch 60]
Пулы — pools.json (как у tk364-evm-multi.py); --only-dirs F — файл со списком SYM (по строке), остальные пропускаются.
Выход: <outdir>/<chain>/<SYM>/<день>.csv (block,ts,logidx,tx,kind,data,txidx,t1,t2,t3) + .done. topic0 считается keccak256 в самом файле (самопроверка: Swap v2)."""
import sys, os, json, time, threading, queue, urllib.request, datetime as dt
M64 = (1 << 64) - 1
RC = [0x1,0x8082,0x800000000000808a,0x8000000080008000,0x808b,0x80000001,0x8000000080008081,0x8000000000008009,0x8a,0x88,0x80008009,0x8000000a,0x8000808b,0x800000000000008b,0x8000000000008089,0x8000000000008003,0x8000000000008002,0x8000000000000080,0x800a,0x800000008000000a,0x8000000080008081,0x8000000000008080,0x80000001,0x8000000080008008]
ROT = [[0,36,3,41,18],[1,44,10,45,2],[62,6,43,15,61],[28,55,25,21,56],[27,20,39,8,14]]
def _rol(x, n): return ((x << n) | (x >> (64 - n))) & M64 if n else x
def keccak256(data: bytes) -> str:
    st = [[0]*5 for _ in range(5)]
    pad = bytearray(data); pad.append(0x01)
    while len(pad) % 136: pad.append(0)
    pad[-1] |= 0x80
    for off in range(0, len(pad), 136):
        for i in range(17):
            st[i % 5][i // 5] ^= int.from_bytes(pad[off+8*i:off+8*i+8], "little")
        for rc in RC:
            C = [st[x][0]^st[x][1]^st[x][2]^st[x][3]^st[x][4] for x in range(5)]
            D = [C[(x-1) % 5] ^ _rol(C[(x+1) % 5], 1) for x in range(5)]
            for x in range(5):
                for y in range(5): st[x][y] ^= D[x]
            B = [[0]*5 for _ in range(5)]
            for x in range(5):
                for y in range(5): B[y][(2*x+3*y) % 5] = _rol(st[x][y], ROT[x][y])
            for x in range(5):
                for y in range(5): st[x][y] = B[x][y] ^ (~B[(x+1) % 5][y] & M64 & B[(x+2) % 5][y])
            st[0][0] ^= rc
    return "0x" + b"".join(st[i % 5][i // 5].to_bytes(8, "little") for i in range(4)).hex()
assert keccak256(b"Swap(address,uint256,uint256,uint256,uint256,address)") == "0xd78ad95fa46c994b6551d0da85fc275fe613ce37657fb8d5e3d130840159d822"
KIND = {keccak256(b"Mint(address,uint256,uint256)"): "mint2", keccak256(b"Burn(address,uint256,uint256,address)"): "burn2",
        keccak256(b"Mint(address,address,int24,int24,uint128,uint256,uint256)"): "mint3",
        keccak256(b"Burn(address,int24,int24,uint128,uint256,uint256)"): "burn3"}
out, CHAIN, POOLFILE, URL, DAYS = sys.argv[1:6]
a = sys.argv[6:]
ONLY = set(open(a[a.index("--only-dirs") + 1]).read().split()) if "--only-dirs" in a else None; SKIP = None
THR = int(a[a.index("--threads") + 1]) if "--threads" in a else 2
BATCH = int(a[a.index("--batch") + 1]) if "--batch" in a else 60
WIN = 100
POOLS = {v["sym"]: v["pool"].lower() for v in json.load(open(POOLFILE)).values() if v["chain"] == CHAIN and len(v["pool"]) == 42 and (ONLY is None or v["sym"] in ONLY)}
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
        for l in rpc("eth_getLogs", [{"address": addrs, "topics": [list(KIND)], "fromBlock": hex(b), "toBlock": hex(e)}]):
            k = KIND.get(l["topics"][0].lower() if l["topics"] else "")
            if not k: continue
            t = l["topics"]
            rows[BY_ADDR[l["address"].lower()]].append(",".join([str(int(l["blockNumber"], 16)), str(int(l["blockTimestamp"], 16)),
                str(int(l["logIndex"], 16)), l["transactionHash"], k, l["data"], str(int(l["transactionIndex"], 16)),
                t[1][-40:] if len(t) > 1 else "", t[2][-40:] if len(t) > 2 else "", t[3][-40:] if len(t) > 3 else ""]))
        b = e + 1
        time.sleep(0.1)
    for s in syms:
        p = os.path.join(out, CHAIN, s); os.makedirs(p, exist_ok=True)
        with open(os.path.join(p, day + ".csv.tmp"), "w", newline="\n") as f:
            f.write("block,ts,logidx,tx,kind,data,txidx,t1,t2\n"); f.write("\n".join(rows[s]) + ("\n" if rows[s] else ""))
        os.replace(os.path.join(p, day + ".csv.tmp"), os.path.join(p, day + ".csv"))
        open(os.path.join(p, day + ".done"), "w").write(f"{len(rows[s])} liq blocks {b0}-{b1} ts=blockTimestamp(s) src=drpc-liq\n")
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
