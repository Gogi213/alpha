#!/usr/bin/env python3
"""TK-393: свопы Ekubo (Starknet) 15–22.10 UTC: starknet_getEvents по Core, ключ Swapped. Выход: <out>/swaps.csv + anchors.csv (блок→время).
usage: tk393-starknet.py <out> <t0> <t1>"""
import json, sys, time, urllib.request, os
U = "https://api.cartridge.gg/x/starknet/mainnet"
CORE = "0x00000005dd3d2f4429af886cd1a3b08289dbcea99a294197e9eb43b0e0325b4b"
SW = "0x157717768aca88da4ac4279765f09f4d0151823d573537fbbeb950cdbd9a870"
out, t0, t1 = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
os.makedirs(out, exist_ok=True)

def rpc(m, p):
    for k in range(6):
        try:
            r = urllib.request.Request(U, json.dumps({"jsonrpc": "2.0", "id": 1, "method": m, "params": p}).encode(), {"content-type": "application/json"})
            j = json.load(urllib.request.urlopen(r, timeout=60))
            if "error" not in j: return j["result"]
            print("rpc error", j["error"], flush=True)
        except Exception as e:
            print("rpc exc", e, flush=True)
        time.sleep(2 * (k + 1))
    raise SystemExit("rpc failed")

def ts(n): return rpc("starknet_getBlockWithTxHashes", [{"block_number": n}])["timestamp"]
head = rpc("starknet_blockNumber", [])
def first_ge(t):
    lo, hi = 1, head
    while lo < hi:
        m = (lo + hi) // 2
        if ts(m) < t: lo = m + 1
        else: hi = m
    return lo
b0, b1 = first_ge(t0), first_ge(t1 + 1) - 1
print("blocks", b0, b1, "head", head, flush=True)
with open(f"{out}/anchors.csv", "w") as f:
    f.write("block,ts\n")
    for b in list(range(b0, b1 + 1, 5000)) + [b1]: f.write(f"{b},{ts(b)}\n")
def i(x): return int(x, 16)
n = 0
with open(f"{out}/swaps.csv", "w") as f:
    f.write("block,txhash,token0,token1,fee,tick_spacing,ext,d0_mag,d0_neg,d1_mag,d1_neg,tick,tick_neg,liq\n")
    cur = b0
    while cur <= b1:
        hi = min(cur + 4999, b1); tok = None
        while True:
            q = {"from_block": {"block_number": cur}, "to_block": {"block_number": hi}, "address": CORE, "keys": [[SW]], "chunk_size": 1000}
            if tok: q["continuation_token"] = tok
            r = rpc("starknet_getEvents", [q])
            for e in r["events"]:
                d = e["data"]
                if len(d) < 21: continue
                f.write(",".join(map(str, [e["block_number"], e["transaction_hash"], d[1], d[2], d[3], i(d[4]), d[5], i(d[12]), i(d[13]), i(d[14]), i(d[15]), i(d[18]), i(d[19]), i(d[20])])) + "\n"); n += 1
            tok = r.get("continuation_token")
            if not tok: break
        cur = hi + 1
        if (cur - b0) % 50000 < 5000: print("cur", cur, "n", n, flush=True)
print("done", n, flush=True)
open(f"{out}/DONE.json", "w").write(json.dumps({"n": n, "b0": b0, "b1": b1}))
