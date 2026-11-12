"""TK-364: общий слой данных — каждый своп DEX → строка с ценой монеты в USD.
usage: tk364-usd.py <root=/data/tk0364> <outdir> [--days 2026-10-15,...] [--only SYM@chain,...]
Вход: <root>/{outw,outw2,outw3,outw4}/<chain|bsc-core>/<SYM>/<день>.csv (block,ts,logidx,tx,kind,data) + pools-файлы (tk364-pools2/3n.json, outw3/pools4.json).
Метаданные пула (token0/1, symbol, decimals) — eth_call latest через публичные RPC по кругу (RPCS), кэш <outdir>/pool-meta.json.
Выход: <outdir>/<chain>/<SYM>/<день>.csv: ts,block,logidx,tx,pool,dex,side,base_amt,price_quote,quote_usd,price_usd,quote_src
  side = сторона трейдера по базовой монете (buy — купил монету, sell — продал); price_quote — цена базовой монеты в котируемой;
  quote_usd — USD-цена котируемой на эту секунду: stable=1; обёртка — главный пул wrap/стейбл той же сети (последний своп ≤ ts); иначе пусто (quote_src=none).
Знак нормализован по token0/token1; для v2 цена из сумм свопа, для v3 — sqrtPriceX96 после свопа."""
import sys, os, csv, json, glob, bisect, time, urllib.request

ROOT, OUT = sys.argv[1:3]
args = sys.argv[3:]
DAYS = args[args.index("--days") + 1].split(",") if "--days" in args else [f"2026-10-{d}" for d in range(15, 22)]
ONLY = set(args[args.index("--only") + 1].split(",")) if "--only" in args else None
RPCS = {"bsc": ["https://bsc-dataseed.binance.org", "https://bsc-rpc.publicnode.com", "https://bsc.drpc.org", "https://bsc-dataseed1.defibit.io"],
        "ethereum": ["https://ethereum-rpc.publicnode.com", "https://eth.llamarpc.com", "https://eth.drpc.org", "https://rpc.ankr.com/eth"],
        "base": ["https://mainnet.base.org", "https://base.drpc.org", "https://base-rpc.publicnode.com"]}
STABLE = {"USDT", "USDC", "BUSD", "USD1", "TUSD", "USDE", "DAI", "FDUSD", "USDS"}
# обёртка -> (цепь, каталог главного пула со знаком оси): главный пул wrap/стейбл той же сети
WRAP_SRC = {("ethereum", "WETH"): "ETH@ethereum", ("ethereum", "ETH"): "ETH@ethereum", ("base", "WETH"): "WETH@base",
            ("base", "ETH"): "WETH@base", ("bsc", "WBNB"): "BNB@bsc", ("bsc", "BTCB"): "BTC@bsc"}

BAD = {}  # (chain, to, data) -> None: неудача кэшируется на запуск, чтобы не повторять на каждый пул-день
_rr = {}
def rpc_call(chain, to, data):
    ck = (chain, to.lower(), data)
    if ck in BAD: return None
    body = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "eth_call", "params": [{"to": to, "data": data}, "latest"]}).encode()
    urls = RPCS[chain]
    for i in range(2 * len(urls)):
        u = urls[(_rr.get(chain, 0) + i) % len(urls)]
        try:
            r = json.load(urllib.request.urlopen(urllib.request.Request(u, body, {"content-type": "application/json", "user-agent": "Mozilla/5.0"}), timeout=15))
            if "error" in r: raise RuntimeError(r["error"])
            _rr[chain] = (_rr.get(chain, 0) + i) % len(urls)
            return r["result"]
        except Exception:
            time.sleep(0.3)
    BAD[ck] = None
    return None

def s_str(h):
    if not h or h == "0x": return ""
    b = bytes.fromhex(h[2:])
    if len(b) >= 64:
        n = int.from_bytes(b[32:64], "big"); return b[64:64 + n].decode("utf8", "ignore")
    return b.rstrip(b"\0").decode("utf8", "ignore")

META_F = os.path.join(OUT, "pool-meta.json")
META = json.load(open(META_F)) if os.path.exists(META_F) else {}

def meta(chain, pool):
    k = f"{chain}:{pool.lower()}"
    if k in META: return META[k]
    t = [rpc_call(chain, pool, "0x0dfe1681"), rpc_call(chain, pool, "0xd21220a7")]
    if not all(t): return None
    toks = ["0x" + x[-40:] for x in t]
    m = {"sym": [], "dec": []}
    for a in toks:
        m["sym"].append(s_str(rpc_call(chain, a, "0x95d89b41")).upper())
        d = rpc_call(chain, a, "0x313ce567"); m["dec"].append(int(d, 16) if d else 18)
    META[k] = m
    return m

def pools_index():
    idx = {}
    for f in ("tk364-pools2.json", "tk364-pools3n.json", "outw3/pools4.json", "pools-wrap.json"):
        p = os.path.join(ROOT, f)
        if os.path.exists(p):
            for k, v in json.load(open(p)).items():
                if len(v["pool"]) <= 42: idx[k] = v
    return idx

def find_dirs(sym, chain):
    r = []
    for o in ("outw", "outw2", "outw3", "outw4", "outw-wrap"):
        for c in ([chain, "bsc-core"] if chain == "bsc" else [chain]):
            d = os.path.join(ROOT, o, c, sym)
            if os.path.isdir(d): r.append(d)
    return r

def words(d):
    d = d[2:]; return [d[i:i + 64] for i in range(0, len(d), 64)]
def s256(h):
    v = int(h, 16); return v - (1 << 256) if v >= 1 << 255 else v

def parse(kind, data, d0, d1):
    """-> (price token1 per token0 с учётом decimals, amount0 со знаком «в пул», decimals-нормированный) либо None"""
    w = words(data)
    try:
        if kind in ("v3", "pcs-v3"):
            a0 = s256(w[0]); sq = int(w[2], 16)
            if sq == 0: return None
            p = (sq / 2 ** 96) ** 2 * 10 ** (d0 - d1)
            return p, a0 / 10 ** d0
        a0i, a1i, a0o, a1o = (int(x, 16) for x in w[:4])
        a0 = a0i - a0o; a1 = a1i - a1o
        if a0 == 0 or a1 == 0: return None
        p = abs(a1 / a0) * 10 ** (d0 - d1)
        return p, a0 / 10 ** d0
    except Exception:
        return None

class Series:
    def __init__(self, pairs): self.ts = [t for t, _ in pairs]; self.px = [p for _, p in pairs]
    def at(self, ts):
        i = bisect.bisect_right(self.ts, ts) - 1
        return self.px[i] if i >= 0 else None

def load_wrap_series(idx, key, days):
    v = idx[key]; m = meta(v["chain"], v["pool"])
    if not m: return None
    q = v["quote"].upper(); s0, s1 = m["sym"]
    base0 = (s1 == q or s1 in STABLE)  # база — token0, если token1 стейбл
    pairs = []
    for d in find_dirs(v["sym"], v["chain"]):
        for day in days:
            f = os.path.join(d, day + ".csv")
            if not os.path.exists(f): continue
            for r in csv.DictReader(open(f)):
                x = parse(r["kind"], r["data"], m["dec"][0], m["dec"][1])
                if x: pairs.append((int(r["ts"]), int(r["logidx"]), x[0] if base0 else 1 / x[0]))
    pairs = sorted(set(pairs))
    return Series([(t, p) for t, _, p in pairs]) if pairs else None

def main():
    os.makedirs(OUT, exist_ok=True)
    idx = pools_index()
    wraps = {}
    for wk, key in WRAP_SRC.items():
        if key in idx and key not in wraps:
            wraps[key] = load_wrap_series(idx, key, DAYS)
    stat = {"pools": 0, "swaps": 0, "no_meta": 0, "no_quote": 0, "no_src": 0}
    for key, v in sorted(idx.items()):
        if ONLY and key not in ONLY: continue
        dirs = find_dirs(v["sym"], v["chain"])
        if not dirs: continue
        m = meta(v["chain"], v["pool"])
        print(key, "meta", "ok" if m else "NONE", flush=True)
        if not m: stat["no_meta"] += 1; continue
        q = v["quote"].upper()
        if q in m["sym"]: qi = m["sym"].index(q)
        elif "W" + q in m["sym"]: qi = m["sym"].index("W" + q)
        elif q.lstrip("W") in m["sym"]: qi = m["sym"].index(q.lstrip("W"))
        else: stat["no_quote"] += 1; continue
        base0 = (qi == 1)
        qsym = m["sym"][qi]
        if qsym in STABLE: qsrc, ser = "stable", None
        elif (v["chain"], qsym) in WRAP_SRC and wraps.get(WRAP_SRC[(v["chain"], qsym)]): qsrc, ser = "pool:" + WRAP_SRC[(v["chain"], qsym)], wraps[WRAP_SRC[(v["chain"], qsym)]]
        else: qsrc, ser = "none", None
        od = os.path.join(OUT, v["chain"], v["sym"]); os.makedirs(od, exist_ok=True)
        for day in DAYS:
            rows = {}
            for d in dirs:
                f = os.path.join(d, day + ".csv")
                if os.path.exists(f):
                    for r in csv.DictReader(open(f)): rows[(int(r["block"]), int(r["logidx"]))] = r
            if not rows: continue
            with open(os.path.join(od, day + ".csv"), "w", newline="") as fo:
                w = csv.writer(fo, lineterminator="\n")
                w.writerow("ts block logidx tx pool dex side base_amt price_quote quote_usd price_usd quote_src".split())
                for (blk, li), r in sorted(rows.items()):
                    x = parse(r["kind"], r["data"], m["dec"][0], m["dec"][1])
                    if not x: continue
                    p01, a0 = x
                    pq = p01 if base0 else 1 / p01
                    # a0 — токен0 в пул; база=token0: a0>0 = трейдер продал базу; база=token1: a0>0 = трейдер купил базу
                    side = ("sell" if a0 > 0 else "buy") if base0 else ("buy" if a0 > 0 else "sell")
                    qu = 1.0 if qsrc == "stable" else (ser.at(int(r["ts"])) if ser else None)
                    pu = pq * qu if qu is not None else None
                    w.writerow([r["ts"], blk, li, r["tx"], v["pool"], v["dex"], side, f"{abs(a0):.10g}", f"{pq:.10g}",
                                "" if qu is None else f"{qu:.8g}", "" if pu is None else f"{pu:.10g}", qsrc])
                    stat["swaps"] += 1
        stat["pools"] += 1
        if stat["pools"] % 20 == 0: json.dump(META, open(META_F, "w"))
        if qsrc == "none": stat["no_src"] += 1
    json.dump(META, open(META_F, "w"))
    json.dump(stat, open(os.path.join(OUT, "DONE.json"), "w"))
    print(stat)

main()
