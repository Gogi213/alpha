#!/usr/bin/env python3
"""Источник для uring_ts_probe: сообщения по 200 Б, экспоненциальные паузы (средняя 1/rate), N секунд. usage: serve.py <port> <rate/s> <секунд>"""
import random
import socket
import sys
import time

port, rate, secs = int(sys.argv[1]), float(sys.argv[2]), float(sys.argv[3])
srv = socket.socket()
srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
srv.bind(("127.0.0.1", port))
srv.listen(1)
c, _ = srv.accept()
c.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
t_end = time.monotonic() + secs
n = 0
nxt = time.monotonic()
while time.monotonic() < t_end:
    nxt += random.expovariate(rate)
    while time.monotonic() < nxt:
        pass
    c.sendall((b"M%07d " % n).ljust(199, b".") + b"\n")
    n += 1
print("sent", n)
c.close()
