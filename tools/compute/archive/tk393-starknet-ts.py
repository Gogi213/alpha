#!/usr/bin/env python3
"""TK-393 (возврат Судьи 26.10): точное время блока для свопов Ekubo: starknet_getBlockWithTxHashes пакетами -> block_ts.csv; swaps_pools.csv + колонка ts -> swaps_pools_ts.csv.
usage: tk393-starknet-ts.py <dir stk> [batch]"""
import csv, json, os, sys, time, urllib.request
U = "https://api.cartridge.gg/x/starknet/mainnet"
d = sys.argv[1]; B = int(sys.argv[2]) if len(sys.argv) > 2 else 50
src, bt = os.path.join(d, "swaps_pools.csv"), os.path.join(d, "block_ts.csv")
blocks = sorted({int(r[0]) for r in csv.reader(open(src)) if r[0].isdigit()})
have = {}
if os.path.exists(bt):
    for r in csv.reader(open(bt)):
        if r[0].isdigit(): have[int(r[0])] = int(r[1])
todo = [b for b in blocks if b not in have]
print("blocks", len(blocks), "todo", len(todo), flush=True)
def batch(bs):
    req = [{"jsonrpc": "2.0", "id": b, "method": "starknet_getBlockWithTxHashes", "params": [{"block_number": b}]} for b in bs]
    for k in range(8):
        try:
            r = urllib.request.Request(U, json.dumps(req).encode(), {"content-type": "application/json", "user-agent": "curl/8"})
            j = json.load(urllib.request.urlopen(r, timeout=90))
            out = {x["id"]: x["result"]["timestamp"] for x in j if "result" in x}
            if len(out) == len(bs): return out
            print("partial", len(out), "of", len(bs), flush=True)
            if out: return out
        except Exception as e: print("exc", e, flush=True)
        time.sleep(2 * (k + 1))
    raise SystemExit("rpc failed")
t = time.time()
with open(bt, "a") as f:
    if not have: f.write("block,ts\n")
    for i in range(0, len(todo), B):
        o = batch(todo[i:i + B])
        for b, ts in o.items(): f.write(f"{b},{ts}\n"); have[b] = ts
        f.flush()
        if (i // B) % 20 == 0: print(i + B, "of", len(todo), round(time.time() - t), "s", flush=True)
missing = [b for b in blocks if b not in have]
if missing: raise SystemExit(f"missing {len(missing)} blocks; rerun")
with open(src) as fi, open(os.path.join(d, "swaps_pools_ts.csv"), "w", newline="") as fo:
    r = csv.reader(fi); w = csv.writer(fo); h = next(r); w.writerow(h + ["ts"])
    for x in r: w.writerow(x + [have[int(x[0])]])
print("done", len(blocks), flush=True)
