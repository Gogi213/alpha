#!/usr/bin/env python3
"""Gate futures WS raw probe: connect, send subscribe messages, record every frame with rx time (ns) to a jsonl; no parsing."""
import asyncio, json, sys, time, websockets

URL = "wss://fx-ws.gateio.ws/v4/ws/usdt"

async def main(subs, secs, out, ping=False, hdr=True):
    extra = {"X-Gate-Size-Decimal": "1"} if hdr else {}
    t0 = time.time()
    async with websockets.connect(URL, additional_headers=extra, max_size=None, ping_interval=None, compression=None) as ws:
        t_conn = time.time()
        f = open(out, "w")
        f.write(json.dumps({"ev": "connected", "ts": time.time_ns(), "dial_s": round(t_conn - t0, 3), "resp_headers": dict(ws.response.headers)}) + "\n")
        for s in subs:
            await ws.send(json.dumps(s))
            f.write(json.dumps({"ev": "sent", "ts": time.time_ns(), "msg": s}) + "\n")
        end = time.time() + secs
        while time.time() < end:
            try:
                m = await asyncio.wait_for(ws.recv(), timeout=max(0.1, end - time.time()))
            except asyncio.TimeoutError:
                break
            except websockets.ConnectionClosed as e:
                f.write(json.dumps({"ev": "closed", "ts": time.time_ns(), "code": e.rcvd.code if e.rcvd else None, "reason": e.rcvd.reason if e.rcvd else None}) + "\n")
                break
            f.write(json.dumps({"ev": "frame", "ts": time.time_ns(), "bin": isinstance(m, bytes), "f": m if isinstance(m, str) else m.hex()}) + "\n")
        f.close()

if __name__ == "__main__":
    subs = json.loads(sys.argv[1]); secs = float(sys.argv[2]); out = sys.argv[3]
    asyncio.run(main(subs, secs, out, hdr=(len(sys.argv) < 5 or sys.argv[4] != "nohdr")))
