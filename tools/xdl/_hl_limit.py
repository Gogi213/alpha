import json, urllib.request, urllib.error, time, datetime, sys
def ts(s): return int(datetime.datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=datetime.timezone.utc).timestamp()*1000)
def post(b):
    r = urllib.request.Request("https://api.hyperliquid.xyz/info", data=json.dumps(b).encode(), headers={"Content-Type":"application/json","User-Agent":"alpha-xdl/1"})
    try:
        with urllib.request.urlopen(r, timeout=25) as f:
            raw = f.read(); return 200, len(json.loads(raw)), dict(f.headers)
    except urllib.error.HTTPError as e:
        return e.code, e.read()[:120].decode("utf-8","replace"), dict(e.headers)
A, B = ts("2026-10-01 00:00"), ts("2026-11-05 00:00") - 1
kind = sys.argv[1]
if kind == "c15": req = {"type":"candleSnapshot","req":{"coin":"BTC","interval":"15m","startTime":A,"endTime":B}}
elif kind == "f": req = {"type":"fundingHistory","coin":"BTC","startTime":A}
elif kind == "small": req = {"type":"candleSnapshot","req":{"coin":"BTC","interval":"15m","startTime":A,"endTime":A+3600000}}
t0 = time.time(); ok = 0; first429 = None
for i in range(1, 140):
    st, body, hdr = post(req)
    el = time.time() - t0
    if st == 200:
        ok += 1
    else:
        print("req #%d at %.1fs -> %s %s" % (i, el, st, body)); print("hdr", {k: v for k, v in hdr.items() if k.lower() not in ("date",)}); first429 = (i, el); break
    time.sleep(0.12)
print(kind, "ok before 429:", ok, "items/req", body if st == 200 else "", "elapsed %.1fs" % (time.time() - t0))
if first429:
    t1 = time.time()
    # recovery: probe a request every 2s
    for k in range(40):
        time.sleep(2)
        st, body, hdr = post(req)
        if st == 200:
            print("recovered after %.1fs since 429" % (time.time() - t1)); break
    else:
        print("not recovered within 80s")
