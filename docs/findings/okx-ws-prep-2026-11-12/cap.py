#!/usr/bin/env python3
"""Проба OKX public WS (G-022, подготовка): подписка и запись кадров как пришли в JSONL+zstd.
  cap.py --out F.jsonl.zst --secs 180 --args-file subs.json [--ip ADDR] [--url wss://ws.okx.com/ws/v5/public] [--batch 100] [--sub-pause 0.2]
Строка 0 — заголовок (connect_s, ip пира, время), далее {"rx_ns": приём, "f": кадр}. Пинг строкой 'ping' раз в 15 с (30 с тишины рвут соединение — документация OKX).
Долгое — только systemd-run."""
import argparse, asyncio, json, socket, time
import websockets, zstandard


async def main(a):
    subs = json.load(open(a.args_file))
    host = a.url.split("/")[2]
    kw = {}
    t0 = time.monotonic()
    if a.ip:
        s = socket.socket(socket.AF_INET6 if ":" in a.ip else socket.AF_INET, socket.SOCK_STREAM)
        s.setblocking(False)
        await asyncio.get_running_loop().sock_connect(s, (a.ip, 443))
        kw = {"sock": s, "server_hostname": host}
    f = open(a.out, "wb")
    c = zstandard.ZstdCompressor(level=3).compressobj()
    n = 0
    async with websockets.connect(a.url, max_size=None, ping_interval=None, compression=None, **kw) as ws:
        peer = ws.transport.get_extra_info("peername")
        f.write(c.compress((json.dumps({"hdr": 1, "rx_ns": time.time_ns(), "url": a.url, "ip_arg": a.ip, "peer": peer[0] if peer else None, "connect_s": round(time.monotonic() - t0, 3), "args": len(subs)}) + "\n").encode()))
        for i in range(0, len(subs), a.batch):
            await ws.send(json.dumps({"id": f"s{i}", "op": "subscribe", "args": subs[i : i + a.batch]}))
            await asyncio.sleep(a.sub_pause)

        async def pinger():
            while True:
                await asyncio.sleep(15)
                await ws.send("ping")

        p = asyncio.create_task(pinger())
        end = time.monotonic() + a.secs
        try:
            while True:
                left = end - time.monotonic()
                if left <= 0:
                    break
                try:
                    fr = await asyncio.wait_for(ws.recv(), timeout=left)
                except asyncio.TimeoutError:
                    break
                f.write(c.compress((json.dumps({"rx_ns": time.time_ns(), "f": fr if isinstance(fr, str) else "hex:" + fr.hex()}) + "\n").encode()))
                n += 1
        finally:
            p.cancel()
    f.write(c.flush())
    f.close()
    print("frames", n)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--secs", type=float, default=180)
    ap.add_argument("--args-file", required=True)
    ap.add_argument("--ip")
    ap.add_argument("--url", default="wss://ws.okx.com/ws/v5/public")
    ap.add_argument("--batch", type=int, default=100)
    ap.add_argument("--sub-pause", type=float, default=0.2)
    asyncio.run(main(ap.parse_args()))
