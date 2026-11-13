#!/usr/bin/env python3
"""G-022 collector-perf (G4): локальный TLS-источник «биржи» с воспроизведением записанного захвата по исходным интервалам.
Нужен для замера ошибки метки приёма: в поле B книги (bsz) пишется время отправки (мкс по модулю 10^10), коллектор (`--tls-ws localhost:<port>`,
`ALPHA_TLS_EXTRA_ROOT=<ca.pem>`) пишет rx; `alpha collect stamperr --dir <root>` печатает rx − отправка. Одно соединение = одна линия захвата (i-я линия — i-я сессия);
каждая линия — свой процесс (fork), чтобы GIL не вносил дрожь в отправку.
usage: tlsws_replay.py <port> <capture.tsv> <cert.pem> <key.pem> [секунд=180]"""
import base64
import hashlib
import os
import re
import select
import socket
import ssl
import struct
import sys
import time

PORT, CAP, CERT, KEY = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
MAXS = float(sys.argv[5]) if len(sys.argv) > 5 else 180.0
EXCH = sys.argv[6] if len(sys.argv) > 6 else "binance"
GUID = b"258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
# носитель времени отправки: поле размера лучшей цены покупки (в записи книги — bsz): Binance/BingX `"B":"…"`, Bybit `"b":[["цена","размер"` , OKX `"bids":[["цена","размер"`
BRE = {
    "binance": re.compile(rb'"B":"([^"]*)"'),
    "bingx": re.compile(rb'"B":"([^"]*)"'),
    "bybit": re.compile(rb'"b":\[\["[^"]*","([^"]*)"'),
    "okx": re.compile(rb'"bids":\[\["[^"]*","([^"]*)"'),
}[EXCH]


def log(*a):
    print(os.getpid(), *a, file=sys.stderr, flush=True)


def ws_frame(payload: bytes, opcode: int = 1) -> bytearray:
    n = len(payload)
    if n < 126:
        h = bytes([0x80 | opcode, n])
    elif n < 65536:
        h = bytes([0x80 | opcode, 126]) + struct.pack(">H", n)
    else:
        h = bytes([0x80 | opcode, 127]) + struct.pack(">Q", n)
    return bytearray(h + payload)


def load():
    lanes = {}
    for line in open(CAP, "rb"):
        parts = line.rstrip(b"\n").split(b"\t", 2)
        if len(parts) != 3:
            continue
        lanes.setdefault(int(parts[0]), []).append((int(parts[1]), parts[2]))
    out = []
    for sender in sorted(lanes):
        rows = lanes[sender]
        t0 = rows[0][0]
        frames = []
        for rx, fr in rows:
            m = BRE.search(fr)
            if m:
                fr = fr[: m.start(1)] + b"0000000000" + fr[m.end(1):]
                buf = ws_frame(fr)
                off = bytes(buf).find(fr[max(0, m.start(1) - 12): m.start(1)] + b"0000000000") + (m.start(1) - max(0, m.start(1) - 12))
            else:
                buf, off = ws_frame(fr), -1
            frames.append((rx - t0, buf, off))
        out.append(frames)
    return out


def read_client_frames(conn: ssl.SSLSocket, buf: bytearray) -> None:
    """Разобрать накопленные кадры клиента (с маской); на Ping ответить Pong."""
    while len(buf) >= 2:
        opcode, ln = buf[0] & 0x0F, buf[1] & 0x7F
        i = 2
        if ln == 126:
            if len(buf) < 4:
                return
            ln, i = struct.unpack(">H", buf[2:4])[0], 4
        elif ln == 127:
            if len(buf) < 10:
                return
            ln, i = struct.unpack(">Q", buf[2:10])[0], 10
        masked = bool(buf[1] & 0x80)
        need = i + (4 if masked else 0) + ln
        if len(buf) < need:
            return
        mask = buf[i:i + 4] if masked else b""
        payload = bytes(buf[i + (4 if masked else 0):need])
        if masked:
            payload = bytes(b ^ mask[k % 4] for k, b in enumerate(payload))
        del buf[:need]
        if opcode == 9:
            conn.sendall(bytes(ws_frame(payload, 10)))
        elif opcode == 8:
            raise EOFError("close")


def serve(conn: ssl.SSLSocket, frames) -> None:
    log('tls ok')
    req = b""
    while b"\r\n\r\n" not in req:
        d = conn.recv(4096)
        if not d:
            return
        req += d
    log('request', len(req))
    key = re.search(rb"Sec-WebSocket-Key: *([^\r\n]+)", req, re.I).group(1).strip()
    acc = base64.b64encode(hashlib.sha1(key + GUID).digest())
    conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: " + acc + b"\r\n\r\n")
    log('101 sent')
    cbuf = bytearray()
    while len(cbuf) < 2:  # ждём подписку клиента
        cbuf += conn.recv(4096)
    log('subscribe', len(cbuf))
    read_client_frames(conn, cbuf)
    t0 = time.perf_counter_ns()
    end = t0 + int(MAXS * 1e9)
    for rel, buf, off in frames:
        target = t0 + rel
        if target > end:
            break
        while True:
            now = time.perf_counter_ns()
            if now >= target:
                break
            if target - now > 300_000:
                r, _, _ = select.select([conn], [], [], min((target - now - 200_000) / 1e9, 0.05))
                if r:
                    try:
                        cbuf += conn.recv(4096)
                    except ssl.SSLWantReadError:
                        pass
                    read_client_frames(conn, cbuf)
        if off >= 0:
            us = (time.time_ns() // 1000) % 10_000_000_000
            buf[off:off + 10] = b"%010d" % us
        conn.sendall(buf)
    # держим соединение, пока клиент не закроет или не кончится время
    while time.perf_counter_ns() < end:
        r, _, _ = select.select([conn], [], [], 0.2)
        if r:
            try:
                d = conn.recv(4096)
            except (ssl.SSLWantReadError, ssl.SSLError):
                continue
            if not d:
                return
            cbuf += d
            read_client_frames(conn, cbuf)


def main():
    lanes = load()
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(CERT, KEY)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", PORT))
    srv.listen(8)
    n = 0
    kids = []
    while n < len(lanes):
        c, _ = srv.accept()
        c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        lane = lanes[n]
        n += 1
        pid = os.fork()
        if pid == 0:
            srv.close()
            try:
                tc = ctx.wrap_socket(c, server_side=True)
                serve(tc, lane)
            except Exception as e:
                print("lane", n, "ended:", e, flush=True)
            os._exit(0)
        c.close()
        kids.append(pid)
    for pid in kids:
        os.waitpid(pid, 0)


main()
