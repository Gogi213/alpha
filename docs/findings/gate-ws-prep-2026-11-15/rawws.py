#!/usr/bin/env python3
"""Gate futures WS: minimal raw TLS+WebSocket client (no library) — sees protocol ping/pong/close frames and can stay silent.
usage: gate_rawws.py <seconds> <out.jsonl> <mode> [subs-json]
 mode: reply  — answer protocol pings with pong
       silent — never answer pings
       appping — answer protocol pings, send futures.ping every 5 s
"""
import json, os, socket, ssl, struct, sys, time, base64, select

HOST = os.environ.get("GATE_HOST", "fx-ws.gateio.ws")
PATH = "/v4/ws/usdt"

def mask_frame(op, payload):
    key = os.urandom(4)
    n = len(payload)
    hdr = bytes([0x80 | op])
    if n < 126: hdr += bytes([0x80 | n])
    elif n < 65536: hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
    else: hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
    return hdr + key + bytes(b ^ key[i % 4] for i, b in enumerate(payload))

def main():
    secs = float(sys.argv[1]); out = open(sys.argv[2], "w"); mode = sys.argv[3]
    subs = json.loads(sys.argv[4]) if len(sys.argv) > 4 else []
    def log(**k):
        k["ts"] = time.time_ns(); out.write(json.dumps(k) + "\n")
    t0 = time.time()
    s = socket.create_connection((HOST, 443), timeout=20); s.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
    t_tcp = time.time()
    ctx = ssl.create_default_context(); ctx.set_alpn_protocols(["http/1.1"])
    s = ctx.wrap_socket(s, server_hostname=HOST)
    t_tls = time.time()
    key = base64.b64encode(os.urandom(16)).decode()
    req = f"GET {PATH} HTTP/1.1\r\nHost: {HOST}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\nX-Gate-Size-Decimal: 1\r\n\r\n"
    s.sendall(req.encode())
    buf = b""
    while b"\r\n\r\n" not in buf: buf += s.recv(4096)
    head, buf = buf.split(b"\r\n\r\n", 1)
    t_ws = time.time()
    log(ev="connected", peer=s.getpeername()[0], tcp_s=round(t_tcp - t0, 3), tls_s=round(t_tls - t_tcp, 3), ws_s=round(t_ws - t_tls, 3), head=head.decode(errors="replace"), tls=s.version(), cipher=s.cipher()[0])
    for m in subs:
        s.sendall(mask_frame(1, json.dumps(m).encode())); log(ev="sent", msg=m)
    end = time.time() + secs; next_ping = time.time() + 5; frag = b""
    s.setblocking(False)
    while time.time() < end:
        r, _, _ = select.select([s], [], [], 0.2)
        now = time.time()
        if mode == "wsping" and now >= next_ping:
            next_ping = now + 5
            s.sendall(mask_frame(9, b"alpha")); log(ev="sent_wsping")
        if mode == "appping" and now >= next_ping:
            next_ping = now + 5
            s.sendall(mask_frame(1, json.dumps({"time": int(now), "channel": "futures.ping"}).encode())); log(ev="sent_appping")
        if not r: continue
        try:
            d = s.recv(262144)
        except ssl.SSLWantReadError:
            continue
        except Exception as e:
            log(ev="error", err=repr(e)); break
        if not d:
            log(ev="eof"); break
        buf += d
        while len(buf) >= 2:
            b0, b1 = buf[0], buf[1]; op = b0 & 15; n = b1 & 127; i = 2
            if n == 126:
                if len(buf) < 4: break
                n = struct.unpack(">H", buf[2:4])[0]; i = 4
            elif n == 127:
                if len(buf) < 10: break
                n = struct.unpack(">Q", buf[2:10])[0]; i = 10
            if len(buf) < i + n: break
            pl = buf[i:i + n]; buf = buf[i + n:]
            if op == 9:
                log(ev="ping", payload=pl.hex(), len=len(pl))
                if mode != "silent": s.sendall(mask_frame(10, pl)); log(ev="pong_sent")
            elif op == 10: log(ev="pong", payload=pl.hex())
            elif op == 8:
                log(ev="close", code=struct.unpack(">H", pl[:2])[0] if len(pl) >= 2 else None, reason=pl[2:].decode(errors="replace")); end = 0
            elif op in (1, 2, 0):
                if op in (1, 2): log(ev="frame", op=op, fin=bool(b0 & 128), f=pl.decode(errors="replace") if op == 1 else pl.hex())
    log(ev="end")
main()
