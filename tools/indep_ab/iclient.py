#!/usr/bin/env python3
"""Independent BingX trade-stream client (G-022 A/B). Not part of the collector: stdlib + `websockets`
sans-IO protocol only. One thread per connection; TLS via ssl.MemoryBIO so that each TLS record is read with its own
recvmsg() and gets the kernel arrival stamp (SO_TIMESTAMPNS, CLOCK_REALTIME) of its newest packet, plus a user-space
stamp (time.time_ns()) right after the read returns.

Output per connection (dir --out): `<name>.tsv` — one line per trade `k_ns \t u_ns \t sym \t id \t T_ms \t E_ms`;
`<name>.ev.jsonl` — connect / subscribed / pong rtt / close events; `<name>.raw.txt` — first raw frames.
"""
import argparse, gzip, json, os, random, socket, ssl, struct, sys, threading, time, zlib

from websockets.client import ClientProtocol
from websockets.frames import Opcode
from websockets.uri import parse_uri

HOST = "open-api-swap.bingx.com"
PATH = "/swap-market"
SO_TIMESTAMPNS = 35  # Linux x86_64
SCM_TIMESTAMPNS = 35
SUB_PERIOD = 0.020  # 20 ms between subscribe messages (documented: no rate limit; 50 msg/s checked 12.11)
STOP = threading.Event()


def now_ns():
    return time.time_ns()


class Conn:
    host = HOST
    path = PATH

    def __init__(self, name, symbols, out_dir, ping_s, ip_idx):
        self.name, self.symbols, self.out_dir, self.ping_s, self.ip_idx = name, symbols, out_dir, ping_s, ip_idx
        self.tsv = open(os.path.join(out_dir, name + ".tsv"), "a", buffering=1 << 16)
        self.ev = open(os.path.join(out_dir, name + ".ev.jsonl"), "a", buffering=1)
        self.raw = open(os.path.join(out_dir, name + ".raw.txt"), "a", buffering=1)
        self.raw_left = 12
        self.session = 0
        self.n_trades = 0
        self.n_frames = 0
        self.n_records = 0
        self.no_stamp = 0
        self.last_flush = time.time()

    def event(self, ev, **kw):
        kw.update(ev=ev, conn=self.name, session=self.session, ts_ns=now_ns())
        self.ev.write(json.dumps(kw, separators=(",", ":")) + "\n")

    # --- raw record IO -------------------------------------------------------------------------
    @staticmethod
    def _recv_exact(sock, n):
        buf = bytearray()
        while len(buf) < n:
            chunk = sock.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("eof")
            buf += chunk
        return bytes(buf)

    def read_record(self):
        """One whole TLS record -> (bytes, kernel_ns or None, user_ns)."""
        hdr = self._recv_exact(self.sock, 5)
        ln = struct.unpack(">H", hdr[3:5])[0]
        body = bytearray()
        k_ns = None
        while len(body) < ln:
            data, anc, _flags, _addr = self.sock.recvmsg(ln - len(body), 64)
            if not data:
                raise ConnectionError("eof")
            body += data
            for level, typ, cdata in anc:
                if level == socket.SOL_SOCKET and typ == SCM_TIMESTAMPNS:
                    sec, nsec = struct.unpack("ll", cdata[:16])
                    k_ns = sec * 1_000_000_000 + nsec  # newest part of the record wins
        u_ns = now_ns()
        self.n_records += 1
        if k_ns is None:
            self.no_stamp += 1
        return hdr + bytes(body), k_ns, u_ns

    def flush_tls(self):
        out = self.outgoing.read()
        if out:
            self.sock.sendall(out)

    # --- connect ---------------------------------------------------------------------------------
    def connect(self):
        self.session += 1
        # deterministic edge spread: connection k of every set takes the k-th address of the sorted DNS answer
        ais = sorted(socket.getaddrinfo(self.host, 443, socket.AF_INET, socket.SOCK_STREAM), key=lambda a: a[4][0])
        ai = ais[self.ip_idx % len(ais)]
        t0 = time.time()
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.setsockopt(socket.SOL_SOCKET, SO_TIMESTAMPNS, 1)
        self.sock.settimeout(15)
        self.sock.connect(ai[4])
        t_tcp = time.time()
        ctx = ssl.create_default_context()
        self.incoming, self.outgoing = ssl.MemoryBIO(), ssl.MemoryBIO()
        self.tls = ctx.wrap_bio(self.incoming, self.outgoing, server_hostname=self.host)
        while True:
            try:
                self.tls.do_handshake()
                break
            except ssl.SSLWantReadError:
                self.flush_tls()
                rec, _, _ = self.read_record()
                self.incoming.write(rec)
        self.flush_tls()
        t_tls = time.time()
        uri = parse_uri(f"wss://{self.host}{self.path}")
        self.proto = ClientProtocol(uri)
        self.proto.send_request(self.proto.connect())
        self.send_proto()
        while True:  # ws handshake response
            rec, _, _ = self.read_record()
            self.feed(rec)
            if self.proto.handshake_exc is not None:
                raise self.proto.handshake_exc
            if self.proto.state.name == "OPEN":
                break
        self.sock.settimeout(None)
        self.event(
            "connected",
            peer=ai[4][0],
            tls=self.tls.version(),
            cipher=self.tls.cipher()[0],
            tcp_ms=round((t_tcp - t0) * 1000, 1),
            tls_ms=round((t_tls - t_tcp) * 1000, 1),
            ws_ms=round((time.time() - t_tls) * 1000, 1),
        )

    def feed(self, rec, k_ns=None, u_ns=None):
        """TLS record -> plaintext -> websocket frames -> handler. Returns list of handled frame count."""
        self.incoming.write(rec)
        while True:
            try:
                plain = self.tls.read(1 << 16)
            except ssl.SSLWantReadError:
                break
            except ssl.SSLZeroReturnError:
                raise ConnectionError("tls close_notify")
            if not plain:
                break
            self.proto.receive_data(plain)
        for ev in self.proto.events_received():
            self.on_frame(ev, k_ns, u_ns)
        self.send_proto()

    def send_proto(self):
        for chunk in self.proto.data_to_send():
            if chunk:
                self.tls.write(chunk)
        self.flush_tls()

    # --- frames ----------------------------------------------------------------------------------------
    def on_frame(self, fr, k_ns, u_ns):
        if getattr(fr, "opcode", None) is None:  # Response / Request events of the handshake
            return
        if fr.opcode == Opcode.PONG:
            t = self.ping_sent.pop(bytes(fr.data), None)
            if t is not None and k_ns is not None:
                self.event("pong", rtt_us=round((k_ns - t) / 1000), rtt_user_us=round((u_ns - t) / 1000))
            return
        if fr.opcode == Opcode.CLOSE:
            self.event("ws_close_frame", data=bytes(fr.data)[:80].decode("utf-8", "replace"))
            return
        if fr.opcode not in (Opcode.BINARY, Opcode.TEXT, Opcode.CONT):
            return
        data = bytes(fr.data)
        if fr.opcode == Opcode.BINARY or data[:2] == b"\x1f\x8b":
            try:
                data = gzip.decompress(data)
            except Exception:
                pass
        self.n_frames += 1
        if self.raw_left > 0:
            self.raw.write(f"{k_ns}\t{data[:300]!r}\n")
            self.raw_left -= 1
        if data == b"Ping":
            self.proto.send_text(b"Pong")
            return
        try:
            msg = json.loads(data)
        except Exception:
            return
        dt = msg.get("dataType") or ""
        if not dt:
            self.n_acks += 1
            if msg.get("code") not in (0, None):
                self.event("sub_err", msg=msg)
            return
        if not dt.endswith("@trade"):
            return
        sym = dt[:-6]
        d = msg.get("data")
        if isinstance(d, dict):
            d = [d]
        for e in d or ():
            self.n_trades += 1
            self.tsv.write(
                f"{k_ns if k_ns is not None else ''}\t{u_ns}\t{sym}\t{e.get('t')}\t{e.get('T')}\t{e.get('E', '')}\n"
            )

    # --- run ------------------------------------------------------------------------------------------------
    def subscribe(self):
        for s in self.symbols:
            if STOP.is_set():
                return
            self.proto.send_text(json.dumps({"id": s + "@trade", "reqType": "sub", "dataType": s + "@trade"}).encode())
            self.send_proto()
            time.sleep(SUB_PERIOD)

    def run_session(self):
        self.ping_sent = {}
        self.n_acks = 0
        self.connect()
        self.subscribe()
        self.event("sub_sent", n=len(self.symbols))
        next_ping = time.time() + self.ping_s
        self.sock.settimeout(1.0)
        subscribed_logged = False
        while not STOP.is_set():
            try:
                hdr = self.sock.recv(5, socket.MSG_PEEK)  # idle wait with timeout, nothing consumed
                if len(hdr) < 5:
                    if not hdr:
                        raise ConnectionError("eof")
                    time.sleep(0.0005)
                    continue
            except socket.timeout:
                hdr = None
            now = time.time()
            if now >= next_ping:
                tag = struct.pack(">Q", now_ns())
                self.ping_sent[tag] = now_ns()
                self.proto.send_ping(tag)
                self.send_proto()
                next_ping = now + self.ping_s
            if now - self.last_flush > 1.0:
                self.tsv.flush()
                self.last_flush = now
            if not subscribed_logged and self.n_acks >= len(self.symbols):
                self.event("subscribed", acks=self.n_acks)
                subscribed_logged = True
            if hdr is None:
                continue
            self.sock.settimeout(15)
            rec, k_ns, u_ns = self.read_record()
            self.sock.settimeout(1.0)
            self.feed(rec, k_ns, u_ns)
        self.event("stopped", trades=self.n_trades, frames=self.n_frames, records=self.n_records, no_stamp=self.no_stamp)

    def loop(self, start_delay):
        time.sleep(start_delay)
        fails = []
        while not STOP.is_set():
            t0 = time.time()
            try:
                self.run_session()
            except Exception as e:  # noqa: BLE001 - log and reconnect
                self.event("closed", err=repr(e)[:200], trades=self.n_trades, frames=self.n_frames, acks=getattr(self, "n_acks", 0))
            try:
                self.sock.close()
            except Exception:  # noqa: BLE001
                pass
            if STOP.is_set():
                break
            fails = [t for t in fails if t0 - t < 600] + [t0]
            if len(fails) > 6:
                self.event("give_up", reason="more than 6 sessions in 10 min")
                break
            STOP.wait(10 + random.random() * 5)
        self.tsv.flush()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", required=True, help="name:symbols_file:connections")
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration-s", type=float, required=True)
    ap.add_argument("--stagger-s", type=float, default=4.0)
    ap.add_argument("--ping-s", type=float, default=5.0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    plan = []
    for spec in a.set:
        name, path, n = spec.split(":")
        syms = [l.strip() for l in open(path) if l.strip()]
        plan.append([(f"{name}{k}", syms, k) for k in range(int(n))])
    order = [c for row in zip(*plan) for c in row] if len({len(p) for p in plan}) == 1 else [c for p in plan for c in p]
    threads, conns = [], []
    for i, (name, syms, k) in enumerate(order):
        c = Conn(name, syms, a.out, a.ping_s, k)
        conns.append(c)
        t = threading.Thread(target=c.loop, args=(i * a.stagger_s,), name=name, daemon=True)
        threads.append(t)
        t.start()
    t_end = time.time() + a.duration_s
    while time.time() < t_end and any(t.is_alive() for t in threads):
        time.sleep(1)
    STOP.set()
    for t in threads:
        t.join(timeout=20)
    for c in conns:
        c.tsv.flush()
    print("done", {c.name: c.n_trades for c in conns})


if __name__ == "__main__":
    main()
