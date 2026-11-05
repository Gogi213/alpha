import sys, os, gzip, csv, glob, datetime, collections, json, urllib.request, urllib.error, statistics, time
root = sys.argv[1]
def rd(p):
    with gzip.open(p, "rt") as f: return list(csv.reader(f))
fmt = lambda t: datetime.datetime.fromtimestamp(int(t)/1000, datetime.timezone.utc).strftime("%m-%d %H:%M")
data = {}
for sub, step in (("candles15", 900000), ("candles", 60000)):
    for p in sorted(glob.glob(os.path.join(root, sub, "*.csv.gz"))):
        sym = os.path.basename(p)[:-7]
        R = rd(p); hdr, rows = R[0], R[1:]
        assert hdr == ["t_open_ms","o","h","l","c","v","quote_v"], hdr
        ts = [int(r[0]) for r in rows]
        dup = len(ts)-len(set(ts)); uns = sum(1 for i in range(1,len(ts)) if ts[i] <= ts[i-1]); notm = sum(1 for t in ts if t % step)
        bpos = bhl = zv = eq = 0
        for r in rows:
            o,h,l,c = map(float, r[1:5]); v = float(r[5])
            if not (o>0 and h>0 and l>0 and c>0): bpos += 1
            if h < max(o,c)-1e-12 or l > min(o,c)+1e-12: bhl += 1
            if v == 0: zv += 1
            if r[6] != "": eq += 1
        gaps = ((ts[-1]-ts[0])//step+1-len(ts)) if ts else 0
        print("%-9s %-10s n=%5d dup=%d unsorted=%d notmult=%d gaps=%d bad_pos=%d bad_hl=%d zero_v=%d nonempty_qv=%d | %s..%s | c0=%s v0=%s" % (sub, sym, len(rows), dup, uns, notm, gaps, bpos, bhl, zv, eq, fmt(ts[0]), fmt(ts[-1]), rows[0][4], rows[0][5]))
        data[(sub, sym)] = {int(r[0]): r for r in rows}
for p in sorted(glob.glob(os.path.join(root, "funding", "*.csv.gz"))):
    sym = os.path.basename(p)[:-7]
    R = rd(p); hdr, rows = R[0], R[1:]
    assert hdr == ["time_ms","rate","interval_h"], hdr
    t = [int(r[0]) for r in rows]
    d = collections.Counter(round((t[i]-t[i-1])/3.6e6, 3) for i in range(1, len(t)))
    nz = sum(1 for r in rows if float(r[1]) != 0)
    print("funding   %-10s n=%d %s..%s interval_h=%s diffs_h=%s nonzero=%d rate0=%s" % (sym, len(rows), fmt(t[0]), fmt(t[-1]), dict(collections.Counter(r[2] for r in rows)), dict(d), nz, rows[0][1]))
# cross-check
def get(url):
    try:
        with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "alpha-xdl/1"}), timeout=20) as f: return json.loads(f.read())
    except urllib.error.HTTPError as e: return {"__http": e.code, "b": e.read()[:100].decode("utf-8","replace")}
    except Exception as e: return {"__err": str(e)[:100]}
def binance(sym, iv, a, b):
    j = get("https://fapi.binance.com/fapi/v1/klines?symbol=%s&interval=%s&startTime=%d&endTime=%d&limit=1500" % (sym, iv, a, b))
    if isinstance(j, dict): return j
    return {int(k[0]): (float(k[4]), float(k[5])) for k in j}
def bybit(sym, iv, a, b):
    j = get("https://api.bybit.com/v5/market/kline?category=linear&symbol=%s&interval=%s&start=%d&end=%d&limit=1000" % (sym, iv.rstrip("m"), a, b))
    if "result" not in j: return j
    return {int(k[0]): (float(k[4]), float(k[5])) for k in j["result"]["list"]}
pairs = {"BTC": ("BTCUSDT", 1), "ETH": ("ETHUSDT", 1), "SOL": ("SOLUSDT", 1), "kPEPE": ("1000PEPEUSDT", 1), "xyz_GOLD": ("XAUUSDT", 1), "xyz_AAPL": ("AAPLUSDT", 1)}
for (sub, sym), rows in sorted(data.items()):
    if sym not in pairs: continue
    iv = "15m" if sub == "candles15" else "1m"
    a, b = min(rows), max(rows)
    if iv == "1m": b = min(b, a + 1400*60000)
    for exn, fn in (("binance", binance), ("bybit", bybit)):
        ref = fn(pairs[sym][0], iv, a, b); time.sleep(0.5)
        if isinstance(ref, dict) and ("__http" in ref or "__err" in ref or "retCode" in ref):
            print("xcheck %-9s %-9s vs %s %s: %s" % (sub, sym, exn, pairs[sym][0], str(ref)[:100])); continue
        common = sorted(set(ref) & set(rows))
        if not common: print("xcheck %-9s %-9s vs %s %s: нет общих свечей (ref %d)" % (sub, sym, exn, pairs[sym][0], len(ref))); continue
        dc = [abs(float(rows[t][4]) / ref[t][0] - 1) * 1e4 for t in common]
        vr = [float(rows[t][5]) / ref[t][1] for t in common if ref[t][1] > 0]
        print("xcheck %-9s %-9s vs %s %s: common=%d  |close diff| bps median=%.1f p95=%.1f max=%.1f ; HL_vol/ref_vol median=%.3f  (HL c=%s ref c=%s)" % (
            sub, sym, exn, pairs[sym][0], len(common), statistics.median(dc), sorted(dc)[int(len(dc)*0.95)-1], max(dc), statistics.median(vr) if vr else float("nan"), rows[common[0]][4], ref[common[0]][0]))
