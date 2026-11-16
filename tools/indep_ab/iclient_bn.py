#!/usr/bin/env python3
"""Independent Binance USD-M futures client (G-022 A/B, :00 investigation). Same stamping as iclient.py
(per-TLS-record SO_TIMESTAMPNS + user stamp), streams chosen by URL, no subscribe messages.
Output `<name>.tsv`: `kind \t k_ns \t u_ns \t sym \t id \t T_ms \t E_ms`, kind b = bookTicker (id = u), t = aggTrade (id = a)."""
import argparse, json, os, threading, time

import iclient
from iclient import Conn, STOP, now_ns
from websockets.frames import Opcode


class BnConn(Conn):
    host = "fstream.binance.com"

    def __init__(self, name, symbols, out_dir, ping_s, ip_idx, endpoint, kind):
        super().__init__(name, symbols, out_dir, ping_s, ip_idx)
        self.path = f"/{endpoint}/stream?streams=" + "/".join(f"{s.lower()}@{kind}" for s in symbols)
        self.kind = kind

    def subscribe(self):
        self.n_acks = len(self.symbols)  # streams are in the URL: nothing to wait for

    def on_frame(self, fr, k_ns, u_ns):
        if getattr(fr, "opcode", None) is None:
            return
        if fr.opcode == Opcode.PONG:
            t = self.ping_sent.pop(bytes(fr.data), None)
            if t is not None and k_ns is not None:
                self.event("pong", rtt_us=round((k_ns - t) / 1000), rtt_user_us=round((u_ns - t) / 1000))
            return
        if fr.opcode == Opcode.CLOSE:
            self.event("ws_close_frame", data=bytes(fr.data)[:80].decode("utf-8", "replace"))
            return
        if fr.opcode not in (Opcode.TEXT, Opcode.BINARY):
            return
        self.n_frames += 1
        data = bytes(fr.data)
        if self.raw_left > 0:
            self.raw.write(f"{k_ns}\t{data[:300]!r}\n")
            self.raw_left -= 1
        try:
            d = json.loads(data).get("data")
        except Exception:
            return
        if not isinstance(d, dict):
            return
        e = d.get("e")
        if e == "bookTicker":
            kind, i = "b", d.get("u")
        elif e == "aggTrade":
            kind, i = "t", d.get("a")
        else:
            return
        self.n_trades += 1
        self.tsv.write(f"{kind}\t{k_ns if k_ns is not None else ''}\t{u_ns}\t{d.get('s')}\t{i}\t{d.get('T')}\t{d.get('E')}\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", required=True, help="comma list, e.g. BTCUSDT,ETHUSDT")
    ap.add_argument("--out", required=True)
    ap.add_argument("--duration-s", type=float, required=True)
    ap.add_argument("--ping-s", type=float, default=5.0)
    a = ap.parse_args()
    os.makedirs(a.out, exist_ok=True)
    syms = a.symbols.split(",")
    conns = [
        BnConn("bnbook0", syms, a.out, a.ping_s, 0, "public", "bookTicker"),
        BnConn("bntrade0", syms, a.out, a.ping_s, 1, "market", "aggTrade"),
    ]
    threads = [threading.Thread(target=c.loop, args=(i * 4.0,), daemon=True) for i, c in enumerate(conns)]
    for t in threads:
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
