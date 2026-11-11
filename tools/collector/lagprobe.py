"""lagprobe MODE SEC OUT [SYMBOL] — лаг rx−E одного потока bookTicker; MODE: wsdef | wsnc | raw."""
import asyncio, json, os, socket, ssl, subprocess, sys, time, base64, struct
mode, sec, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
sym = sys.argv[4] if len(sys.argv) > 4 else "btcusdt"
host, path = "fstream.binance.com", f"/ws/{sym}@bookTicker"
rec = []   # (rx, lag_ms)
ss = {}
def ssinfo(lport, tag):
    r = subprocess.run(["ss", "-tin", f"sport = :{lport}"], capture_output=True, text=True).stdout.split("\n")
    ss[tag] = " ".join(x.strip() for x in r[1:3])[:600]
def finish(lport, cpu0, t0, extra):
    ssinfo(lport, "end")
    lags = sorted(l for _, l in rec); n = len(lags)
    q = lambda p: round(lags[min(n - 1, int(n * p))], 1) if n else None
    b = {}
    for rx, l in rec: b.setdefault(int((rx - t0) // 10), []).append(l)
    series = [round(sorted(v)[len(v) // 2]) for k, v in sorted(b.items())]
    cpu = time.process_time() - cpu0
    json.dump({"mode": mode, "sym": sym, "lport": lport, "sec": sec, "msgs": n, "cpu_pct": round(100 * cpu / sec, 1),
               "lag_ms": {"p50": q(.5), "p90": q(.9), "p99": q(.99), "max": round(lags[-1], 1) if n else None},
               "series10s_p50": series, "ss": ss, **extra}, open(out, "w"))
if mode == "raw":
    ctx = ssl.create_default_context()
    s = ctx.wrap_socket(socket.create_connection((host, 443)), server_hostname=host)
    lport = s.getsockname()[1]
    key = base64.b64encode(os.urandom(16)).decode()
    s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
               f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
    buf = bytearray()
    while b"\r\n\r\n" not in buf: buf += s.recv(65536)
    i = buf.index(b"\r\n\r\n"); del buf[:i + 4]
    t0 = time.time(); cpu0 = time.process_time(); mid = False; nrecv = 0
    while time.time() - t0 < sec:
        d = s.recv(262144); nrecv += 1; rx = time.time(); buf += d
        if not mid and rx - t0 > sec / 2: ssinfo(lport, "mid"); mid = True
        while len(buf) >= 2:
            op = buf[0] & 15; ln = buf[1] & 127; o = 2
            if ln == 126:
                if len(buf) < 4: break
                ln = struct.unpack(">H", buf[2:4])[0]; o = 4
            elif ln == 127:
                if len(buf) < 10: break
                ln = struct.unpack(">Q", buf[2:10])[0]; o = 10
            if len(buf) < o + ln: break
            pl = bytes(buf[o:o + ln]); del buf[:o + ln]
            if op == 1:
                j = pl.find(b'"E":')
                if j >= 0: rec.append((rx, rx * 1000 - int(pl[j + 4:pl.index(b",", j)])))
            elif op == 9:
                m = os.urandom(4); s.sendall(bytes([0x8A, 0x80 | len(pl)]) + m + bytes(c ^ m[k % 4] for k, c in enumerate(pl)))
    finish(lport, cpu0, t0, {"recv_calls": nrecv})
else:
    import websockets
    async def main():
        kw = {"compression": None} if mode == "wsnc" else {}
        async with websockets.connect(f"wss://{host}{path}", ping_interval=20, ping_timeout=20, **kw) as ws:
            lport = ws.transport.get_extra_info("sockname")[1]
            t0 = time.time(); cpu0 = time.process_time(); mid = False
            while time.time() - t0 < sec:
                m = await asyncio.wait_for(ws.recv(), timeout=10); rx = time.time()
                if not mid and rx - t0 > sec / 2: ssinfo(lport, "mid"); mid = True
                rec.append((rx, rx * 1000 - json.loads(m)["E"]))
            finish(lport, cpu0, t0, {"ver": websockets.__version__})
    asyncio.run(main())
