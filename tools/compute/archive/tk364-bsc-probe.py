"""TK-364 проба: свопы одного пула PancakeV3 на BSC через публичный RPC (eth_getLogs) за сутки.
usage: tk364-bsc-probe.py <rpc> <pool> <YYYY-MM-DD> <out.csv>"""
import sys, json, time, urllib.request, datetime as dt

RPC, POOL, DAY, OUT = sys.argv[1:5]
TOPIC = "0x19b47279256b2a23a1665c810c8d55a1758940ee09377d4f8d26497a3577dc83"  # Pancake V3 Swap
N = [0]

def rpc(method, params, tries=4):
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params}).encode()
    for i in range(tries):
        N[0] += 1
        try:
            req = urllib.request.Request(RPC, body, {"content-type": "application/json", "user-agent": "Mozilla/5.0"})
            r = json.load(urllib.request.urlopen(req, timeout=30))
            if "error" in r:
                raise RuntimeError(r["error"])
            return r["result"]
        except Exception as e:
            err = e
            time.sleep(1 + i)
    raise err

def blk_ts(n):
    return int(rpc("eth_getBlockByNumber", [hex(n), False])["timestamp"], 16)

def find_block(ts):
    lo, hi = 1, int(rpc("eth_blockNumber", []), 16)
    while lo < hi:
        mid = (lo + hi) // 2
        if blk_ts(mid) < ts: lo = mid + 1
        else: hi = mid
    return lo

def s256(h):
    v = int(h, 16)
    return v - (1 << 256) if v >> 255 else v

d0 = int(dt.datetime.strptime(DAY, "%Y-%m-%d").replace(tzinfo=dt.timezone.utc).timestamp())
b0, b1 = find_block(d0), find_block(d0 + 86400)
print(f"{DAY} blocks {b0}..{b1} ({b1-b0})", flush=True)
step = int(sys.argv[5]) if len(sys.argv) > 5 else 5000
ts_cache, rows, b = {}, [], b0
while b < b1:
    e = min(b + step - 1, b1 - 1)
    logs = rpc("eth_getLogs", [{"fromBlock": hex(b), "toBlock": hex(e), "address": POOL, "topics": [TOPIC]}])
    for l in logs:
        bn = int(l["blockNumber"], 16)
        data = l["data"][2:]
        a0, a1 = s256(data[0:64]), s256(data[64:128])
        sqrtp = int(data[128:192], 16)
        rows.append((bn, int(l["logIndex"], 16), a0, a1, sqrtp, l["transactionHash"]))
    b = e + 1
bns = sorted({r[0] for r in rows})
for bn in bns:
    ts_cache[bn] = blk_ts(bn)
with open(OUT, "w", newline="\n") as f:
    f.write("block,ts,logidx,amount0,amount1,sqrtP,tx\n")
    for r in rows:
        f.write(f"{r[0]},{ts_cache[r[0]]},{r[1]},{r[2]},{r[3]},{r[4]},{r[5]}\n")
print(f"swaps {len(rows)} blocks_with_swaps {len(bns)} rpc_calls {N[0]}")
