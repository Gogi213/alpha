#!/usr/bin/env python3
"""Сетевой сторож srv (G-022, 13.11): раз в секунду RTT TCP-connect (без данных) до одного адреса края каждой биржи и ICMP до шлюза srv и
нейтральной цели. Пишет /data/netwatch/<день UTC>.jsonl — по строке на замер, для обращения к хостеру: когда затор общий (все цели разом),
а когда — путь к одной бирже. Только стандартная библиотека; ICMP — сырой сокет (нужен root); один поток на цель, нагрузка < 1 % ядра.

Строка: {"ts":<unix с>,"n":"<цель>","rtt":<мс или null>,"e":"<ошибка, если rtt null>"}; в начале файла и при смене адреса — {"meta":…}.
"""
import argparse
import json
import os
import select
import socket
import struct
import subprocess
import threading
import time

# цель: (имя, хост, порт) — адрес выбирается один раз (первый IPv4 имени) и держится; смена — после 30 неудач подряд
TCP_TARGETS = [
    ("binance", "fstream.binance.com", 443),   # AWS, прямой путь
    ("bybit", "stream.bybit.com", 443),        # CloudFront 54.230.172.x
    ("bingx", "open-api-swap.bingx.com", 443),  # CloudFront
    ("okx", "ws.okx.com", 443),                # Cloudflare
]
NEUTRAL = ("neutral", "8.8.8.8")
TCP_TIMEOUT = 3.0
ICMP_TIMEOUT = 2.0
PERIOD = 1.0
RESOLVE_AFTER_FAILS = 30


def resolve4(host):
    infos = socket.getaddrinfo(host, None, socket.AF_INET, socket.SOCK_STREAM)
    return infos[0][4][0]


def default_gateway():
    out = subprocess.run(["ip", "-4", "route", "show", "default"], capture_output=True, text=True, check=True).stdout.split()
    return out[out.index("via") + 1]


def checksum(b):
    if len(b) % 2:
        b += b"\0"
    s = sum(struct.unpack("!%dH" % (len(b) // 2), b))
    s = (s >> 16) + (s & 0xFFFF)
    s += s >> 16
    return ~s & 0xFFFF


class Out:
    """Дневной файл jsonl; запись под замком, строки сбрасываются сразу (сторож живёт неделями, потеря хвоста при сбое недопустима)."""

    def __init__(self, root):
        self.root, self.day, self.f, self.lock = root, None, None, threading.Lock()
        os.makedirs(root, exist_ok=True)

    def write(self, obj):
        line = json.dumps(obj, separators=(",", ":"), ensure_ascii=False) + "\n"
        day = time.strftime("%Y-%m-%d", time.gmtime())
        with self.lock:
            if day != self.day:
                if self.f:
                    self.f.close()
                self.f = open(os.path.join(self.root, day + ".jsonl"), "a", buffering=1, encoding="utf-8")
                self.day = day
            self.f.write(line)


def paced(period, step):
    """Вызывает step() раз в period с, без накопления опозданий: долгий замок (таймаут) пропускает такты, а не сыплет их пачкой."""
    nxt = time.monotonic()
    while True:
        step()
        nxt += period
        now = time.monotonic()
        if nxt < now:
            nxt = now + period - ((now - nxt) % period)
        time.sleep(max(0.0, nxt - time.monotonic()))


def tcp_worker(out, name, host, port):
    state = {"ip": None, "fails": 0}

    def pick():
        try:
            state["ip"] = resolve4(host)
            out.write({"meta": {"n": name, "host": host, "ip": state["ip"], "port": port, "ts": round(time.time(), 3)}})
        except OSError as e:
            state["ip"] = None
            out.write({"ts": round(time.time(), 3), "n": name, "rtt": None, "e": "resolve:" + str(e)[:60]})

    def step():
        if state["ip"] is None:
            pick()
            if state["ip"] is None:
                return
        t0 = time.monotonic()
        ts = time.time()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.settimeout(TCP_TIMEOUT)
        try:
            s.connect((state["ip"], port))
            rtt = round((time.monotonic() - t0) * 1000, 2)
            out.write({"ts": round(ts, 3), "n": name, "rtt": rtt})
            state["fails"] = 0
        except OSError as e:
            out.write({"ts": round(ts, 3), "n": name, "rtt": None, "e": ("timeout" if isinstance(e, socket.timeout) else str(e)[:60])})
            state["fails"] += 1
            if state["fails"] >= RESOLVE_AFTER_FAILS:
                state["fails"] = 0
                state["ip"] = None
        finally:
            s.close()

    paced(PERIOD, step)


def icmp_worker(out, name, ip):
    sock = socket.socket(socket.AF_INET, socket.SOCK_RAW, socket.IPPROTO_ICMP)
    ident = (os.getpid() + hash(name)) & 0xFFFF
    seq = [0]
    out.write({"meta": {"n": name, "ip": ip, "icmp": True, "ts": round(time.time(), 3)}})

    def step():
        seq[0] = (seq[0] + 1) & 0xFFFF
        payload = struct.pack("!d", time.time()) + b"alphanetwatch"
        hdr = struct.pack("!BBHHH", 8, 0, 0, ident, seq[0])
        pkt = struct.pack("!BBHHH", 8, 0, checksum(hdr + payload), ident, seq[0]) + payload
        t0 = time.monotonic()
        ts = time.time()
        sock.sendto(pkt, (ip, 0))
        while True:
            left = ICMP_TIMEOUT - (time.monotonic() - t0)
            if left <= 0:
                out.write({"ts": round(ts, 3), "n": name, "rtt": None, "e": "timeout"})
                return
            r, _, _ = select.select([sock], [], [], left)
            if not r:
                continue
            data, addr = sock.recvfrom(1024)
            body = data[(data[0] & 0x0F) * 4:]
            if len(body) >= 8 and body[0] == 0 and addr[0] == ip and struct.unpack("!HH", body[4:8]) == (ident, seq[0]):
                out.write({"ts": round(ts, 3), "n": name, "rtt": round((time.monotonic() - t0) * 1000, 2)})
                return

    paced(PERIOD, step)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/data/netwatch")
    a = ap.parse_args()
    out = Out(a.out)
    threads = [threading.Thread(target=tcp_worker, args=(out, n, h, p), daemon=True, name="tcp-" + n) for n, h, p in TCP_TARGETS]
    threads.append(threading.Thread(target=icmp_worker, args=(out, "gateway", default_gateway()), daemon=True, name="icmp-gw"))
    threads.append(threading.Thread(target=icmp_worker, args=(out, NEUTRAL[0], NEUTRAL[1]), daemon=True, name="icmp-neutral"))
    for t in threads:
        t.start()
    while True:
        time.sleep(3600)
        dead = [t.name for t in threads if not t.is_alive()]
        if dead:  # поток упал — юнит перезапустится (Restart=always)
            raise SystemExit("потоки упали: " + ",".join(dead))


if __name__ == "__main__":
    main()
