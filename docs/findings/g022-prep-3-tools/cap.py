#!/usr/bin/env python3
"""Проба WS (G-022, подготовка Bitget/HTX/BingX): подписка и запись кадров как пришли в JSONL+zstd.
  cap.py --out F.jsonl.zst --secs 180 --url wss://... --msgs-file msgs.json [--ip ADDR] [--sub-pause 0.2]
         [--ping none|text|proto] [--ping-every 30] [--gz] [--htx-pong]  (--htx-pong отвечает и на {"ping":ts} рынка, и на {"op":"ping","ts":…} уведомлений) [--bingx-pong] [--ping-text ping]
msgs.json — список сообщений подписки (строка — как есть, объект — json.dumps), отправляются по одному с паузой.
Строка 0 — заголовок (connect_s, ip пира, время), далее {"rx_ns": приём (нс UTC), "f": кадр (текст; для --gz — распакованный)};
для двоичных кадров добавляется "z": размер сжатого, у первых 30 — "b64": сжатый кадр как пришёл (сырьё для тестов разбора).
Последняя строка при обрыве — {"closed": код, "reason": причина, "rx_ns": …}. Долгое — только systemd-run."""
import argparse, asyncio, base64, json, socket, time, zlib
import websockets, zstandard


def unz(b):
    for wbits in (31, 15, -15):
        try:
            return zlib.decompressobj(wbits).decompress(b).decode("utf-8")
        except Exception:
            pass
    return "hex:" + b.hex()


async def main(a):
    msgs = json.load(open(a.msgs_file))
    hp = a.url.split("/")[2]
    host = hp.split(":")[0]
    port = int(hp.split(":")[1]) if ":" in hp else 443
    kw = {}
    t0 = time.monotonic()
    if a.ip:
        s = socket.socket(socket.AF_INET6 if ":" in a.ip else socket.AF_INET, socket.SOCK_STREAM)
        s.setblocking(False)
        await asyncio.get_running_loop().sock_connect(s, (a.ip, port))
        kw = {"sock": s, "server_hostname": host}
    f = open(a.out, "wb")
    c = zstandard.ZstdCompressor(level=3).compressobj()

    def w(o):
        f.write(c.compress((json.dumps(o, ensure_ascii=False) + "\n").encode()))

    n = nb = 0
    try:
        async with websockets.connect(a.url, max_size=None, ping_interval=None, compression=None, **kw) as ws:
            peer = ws.transport.get_extra_info("peername")
            w({"hdr": 1, "rx_ns": time.time_ns(), "url": a.url, "ip_arg": a.ip, "peer": peer[0] if peer else None, "connect_s": round(time.monotonic() - t0, 3), "msgs": len(msgs)})

            async def subscriber():
                for m in msgs:
                    await ws.send(m if isinstance(m, str) else json.dumps(m))
                    await asyncio.sleep(a.sub_pause)

            async def pinger():
                while True:
                    await asyncio.sleep(a.ping_every)
                    if a.ping == "text":
                        await ws.send(a.ping_text)
                    else:
                        await ws.ping()

            tasks = [asyncio.create_task(subscriber())]
            if a.ping in ("text", "proto"):
                tasks.append(asyncio.create_task(pinger()))
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
                    rx = time.time_ns()
                    rec = {"rx_ns": rx}
                    if isinstance(fr, bytes):
                        rec["z"] = len(fr)
                        if a.gz:
                            if nb < 30:
                                rec["b64"] = base64.b64encode(fr).decode()
                                nb += 1
                            fr = unz(fr)
                        else:
                            fr = "hex:" + fr.hex()
                    rec["f"] = fr
                    w(rec)
                    n += 1
                    if a.htx_pong and fr.startswith('{"ping"'):
                        await ws.send(json.dumps({"pong": json.loads(fr)["ping"]}))
                    elif a.htx_pong and fr.startswith('{"op":"ping"'):
                        await ws.send(json.dumps({"op": "pong", "ts": json.loads(fr)["ts"]}))
                    elif a.bingx_pong and fr == "Ping":
                        await ws.send("Pong")
            finally:
                for t in tasks:
                    t.cancel()
    except websockets.ConnectionClosed as e:
        w({"closed": e.rcvd.code if e.rcvd else None, "reason": e.rcvd.reason if e.rcvd else None, "sent": e.sent.code if e.sent else None, "rx_ns": time.time_ns(), "after_s": round(time.monotonic() - t0, 2)})
        print("closed", e)
    except Exception as e:
        w({"error": repr(e), "rx_ns": time.time_ns(), "after_s": round(time.monotonic() - t0, 2)})
        print("error", repr(e))
    f.write(c.flush())
    f.close()
    print("frames", n, "cpu_s", round(time.process_time(), 1), "wall_s", round(time.monotonic() - t0, 1))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--secs", type=float, default=180)
    ap.add_argument("--url", required=True)
    ap.add_argument("--msgs-file", required=True)
    ap.add_argument("--ip")
    ap.add_argument("--sub-pause", type=float, default=0.2)
    ap.add_argument("--ping", default="none", choices=["none", "text", "proto"])
    ap.add_argument("--ping-every", type=float, default=30)
    ap.add_argument("--ping-text", default="ping")
    ap.add_argument("--gz", action="store_true")
    ap.add_argument("--htx-pong", action="store_true")
    ap.add_argument("--bingx-pong", action="store_true")
    asyncio.run(main(ap.parse_args()))
