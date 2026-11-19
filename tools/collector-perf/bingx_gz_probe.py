#!/usr/bin/env python3
"""G-022 collector-perf: какие блоки deflate шлёт BingX (фиксированный Хаффман, динамический, сжатые без сжатия) и повторяется ли описание динамического дерева между кадрами.
usage: bingx_gz_probe.py [секунд=20] [число тем=40]; нужен пакет websockets. Печатает: кадров, типы блока (stored/fixed/dynamic), сжато/разжато (медианы),
число различных описаний дерева (байты заголовка динамического блока до таблицы кодов) и долю кадров с самым частым описанием."""
import asyncio, collections, gzip, json, ssl, struct, sys, time, zlib
import websockets

SECS = int(sys.argv[1]) if len(sys.argv) > 1 else 20
NSYM = int(sys.argv[2]) if len(sys.argv) > 2 else 40


def deflate_start(frame):
    # RFC 1952: заголовок 10 байт + необязательные поля
    flg = frame[3]
    pos = 10
    if flg & 4:
        pos += 2 + struct.unpack("<H", frame[pos:pos + 2])[0]
    if flg & 8:
        pos = frame.index(0, pos) + 1
    if flg & 16:
        pos = frame.index(0, pos) + 1
    if flg & 2:
        pos += 2
    return pos


async def main():
    ctx = ssl.create_default_context()
    async with websockets.connect("wss://open-api-swap.bingx.com/swap-market", ssl=ctx, max_size=None) as ws:
        # символы: берём из REST-контрактов
        import urllib.request
        d = json.load(urllib.request.urlopen("https://open-api.bingx.com/openApi/swap/v2/quote/contracts", timeout=10))["data"]
        syms = [c["symbol"] for c in d][:NSYM]
        for s in syms:
            for kind in ("bookTicker", "trade", "markPrice"):
                await ws.send(json.dumps({"id": f"{s}@{kind}", "reqType": "sub", "dataType": f"{s}@{kind}"}))
            await asyncio.sleep(0.02)
        end = time.time() + SECS
        types = collections.Counter(); comp = []; raw = []; heads = collections.Counter(); n = 0
        while time.time() < end:
            try:
                f = await asyncio.wait_for(ws.recv(), 2)
            except asyncio.TimeoutError:
                continue
            if isinstance(f, str):
                continue
            n += 1
            p = deflate_start(f)
            b0 = f[p]
            btype = (b0 >> 1) & 3
            types[{0: "stored", 1: "fixed", 2: "dynamic"}.get(btype, "?")] += 1
            comp.append(len(f)); raw.append(struct.unpack("<I", f[-4:])[0])
            if btype == 2:
                # HLIT(5) HDIST(5) HCLEN(4) после трёх бит заголовка блока + таблица длин кодов: берём первые 12 байт описания как отпечаток
                heads[bytes(f[p:p + 12])] += 1
        comp.sort(); raw.sort()
        med = lambda v: v[len(v) // 2] if v else None
        print(f"кадров {n}, типы блока {dict(types)}, сжато (медиана) {med(comp)} Б, разжато (медиана) {med(raw)} Б, "
              f"различных отпечатков дерева {len(heads)}, самый частый — {max(heads.values()) / max(1, sum(heads.values())):.1%} динамических кадров")


asyncio.run(main())
