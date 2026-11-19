#!/usr/bin/env python3
"""G-022 collector-perf probe: kernel RX timestamp (SO_TIMESTAMPNS) vs user-space stamp on live Binance book connections (reconnects on close).
Measures the first stage of the latency budget (skb arrival in kernel -> thread has the data) with plain blocking recv (SO_RCVTIMEO, as the collector does),
optionally with SO_BUSY_POLL, and prints thread CPU. usage: ts_probe.py <seconds> <busy_poll_us|0> [streams]"""
import base64
import os
import resource
import socket
import ssl
import struct
import sys
import time

SECS = float(sys.argv[1])
BUSY = int(sys.argv[2])
NSTREAM = int(sys.argv[3]) if len(sys.argv) > 3 else 32
HOST = "fstream.binance.com"
SYMS = ["btcusdt", "ethusdt", "solusdt", "xrpusdt", "dogeusdt", "bnbusdt", "adausdt", "trxusdt", "linkusdt", "avaxusdt", "suiusdt", "ltcusdt", "dotusdt", "bchusdt",
        "nearusdt", "aptusdt", "uniusdt", "atomusdt", "filusdt", "arbusdt", "opusdt", "injusdt", "tiausdt", "seiusdt", "wldusdt", "ordiusdt", "pepeusdt", "wifusdt",
        "bonkusdt", "fetusdt", "rndrusdt", "tonusdt"][:NSTREAM]
SO_TIMESTAMPNS = 35


def connect():
    s = socket.create_connection((HOST, 443), timeout=10)
    s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    inb, outb = ssl.MemoryBIO(), ssl.MemoryBIO()
    tls = ssl.create_default_context().wrap_bio(inb, outb, server_hostname=HOST)
    while True:
        try:
            tls.do_handshake()
            break
        except ssl.SSLWantReadError:
            if outb.pending:
                s.sendall(outb.read())
            d = s.recv(65536)
            if not d:
                raise EOFError("closed during handshake")
            inb.write(d)
    if outb.pending:
        s.sendall(outb.read())
    key = base64.b64encode(os.urandom(16)).decode()
    streams = "/".join(f"{x}@bookTicker" for x in SYMS)
    req = (f"GET /stream?streams={streams} HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
           f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    tls.write(req.encode())
    s.sendall(outb.read())
    buf = b""
    while b"\r\n\r\n" not in buf:
        try:
            buf += tls.read(65536)
        except ssl.SSLWantReadError:
            d = s.recv(65536)
            if not d:
                raise EOFError("closed before upgrade")
            inb.write(d)
    if b" 101 " not in buf.split(b"\r\n", 1)[0]:
        raise EOFError("upgrade refused: " + buf.split(b"\r\n", 1)[0].decode(errors="replace"))
    s.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS, 1)
    if BUSY:
        s.setsockopt(socket.SOL_SOCKET, 46, BUSY)  # SO_BUSY_POLL
    s.settimeout(None)  # blocking recv inside the kernel (SO_RCVTIMEO); Python's own timeout would poll() first
    s.setsockopt(socket.SOL_SOCKET, socket.SO_RCVTIMEO, struct.pack("ll", 0, 200000))
    return s, inb, tls


deltas = []
nrecv = 0
nbytes = 0
conns = 0
t_end = time.monotonic() + SECS
c0 = time.thread_time()
w0 = time.monotonic()
r0 = resource.getrusage(resource.RUSAGE_SELF)
while time.monotonic() < t_end:
    try:
        s, inb, tls = connect()
    except (EOFError, OSError, ssl.SSLError) as e:
        print("connect:", e, flush=True)
        time.sleep(1)
        continue
    conns += 1
    try:
        while time.monotonic() < t_end:
            try:
                data, anc, flags, addr = s.recvmsg(65536, 256)
            except (socket.timeout, BlockingIOError):
                continue
            t_user = time.clock_gettime_ns(time.CLOCK_REALTIME)
            if not data:
                break
            nrecv += 1
            nbytes += len(data)
            for lvl, typ, cd in anc:
                if lvl == socket.SOL_SOCKET and typ == SO_TIMESTAMPNS:
                    sec, nsec = struct.unpack("ll", cd[:16])
                    deltas.append((t_user - (sec * 1_000_000_000 + nsec)) / 1000.0)
            inb.write(data)
            closed = False
            while True:
                try:
                    if not tls.read(65536):
                        closed = True
                        break
                except ssl.SSLWantReadError:
                    break
                except ssl.SSLError:
                    closed = True
                    break
            if closed:
                break
    finally:
        s.close()
cpu = time.thread_time() - c0
wall = time.monotonic() - w0
r1 = resource.getrusage(resource.RUSAGE_SELF)
deltas.sort()


def pct(p):
    return deltas[min(len(deltas) - 1, int(len(deltas) * p))] if deltas else float("nan")


print(f"busy_poll={BUSY}us streams={NSTREAM} wall {wall:.1f}s connections {conns} recv calls {nrecv} ({nrecv / wall:.0f}/s) bytes {nbytes / wall / 1e3:.0f} kB/s; kernel-stamp deltas n={len(deltas)}")
print(f"user - kernel rx stamp, us: p50 {pct(.5):.1f} p90 {pct(.9):.1f} p99 {pct(.99):.1f} p99.9 {pct(.999):.1f} max {deltas[-1] if deltas else float('nan'):.1f}")
print(f"thread CPU {cpu:.2f} s over {wall:.1f} s = {cpu / wall * 100:.1f} %  (user {r1.ru_utime - r0.ru_utime:.2f} sys {r1.ru_stime - r0.ru_stime:.2f}); vol ctx sw {r1.ru_nvcsw - r0.ru_nvcsw} invol {r1.ru_nivcsw - r0.ru_nivcsw}")
