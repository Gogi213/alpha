"""Есть ли RPI у монет нашего пула: подписка на orderbook.rpi и orderbook.50.

Запускает stdlib-клиент WebSocket прямо на сервере (там сеть до Bybit живая),
локальная машина только релеит. Ничего не пишет в прод: отдельный сокет, только чтение.
"""
import os
import paramiko

REMOTE = r'''
import base64, json, os, random, socket, ssl, struct, time

ARGS = ["orderbook.rpi.LSKUSDT", "orderbook.50.LSKUSDT", "orderbook.rpi.BTCUSDT"]
SECONDS = float(os.environ.get("RPI_SECONDS", "25"))

key = base64.b64encode(bytes(random.getrandbits(8) for _ in range(16))).decode()
req = (
    "GET /v5/public/linear HTTP/1.1\r\n"
    "Host: stream.bybit.com\r\n"
    "Upgrade: websocket\r\n"
    "Connection: Upgrade\r\n"
    f"Sec-WebSocket-Key: {key}\r\n"
    "Sec-WebSocket-Version: 13\r\n\r\n"
)
ctx = ssl.create_default_context()
s = ctx.wrap_socket(socket.create_connection(("stream.bybit.com", 443), timeout=15),
                    server_hostname="stream.bybit.com")
s.sendall(req.encode())
buf = b""
while b"\r\n\r\n" not in buf:
    buf += s.recv(4096)
head, rest = buf.split(b"\r\n\r\n", 1)
print("handshake:", head.split(b"\r\n")[0].decode(errors="replace"))

def send_text(payload):
    data = payload.encode()
    mask = bytes(random.getrandbits(8) for _ in range(4))
    n = len(data)
    frame = bytearray([0x81])
    if n < 126:
        frame.append(0x80 | n)
    elif n < 65536:
        frame.append(0x80 | 126); frame += struct.pack(">H", n)
    else:
        frame.append(0x80 | 127); frame += struct.pack(">Q", n)
    frame += mask
    frame += bytes(b ^ mask[i % 4] for i, b in enumerate(data))
    s.sendall(bytes(frame))

send_text(json.dumps({"op": "subscribe", "args": ARGS}))

stats = {a: {"msgs": 0, "snap": 0, "delta": 0, "lv": 0, "rpi_pos": 0,
             "rpi_sum": 0.0, "vis_sum": 0.0, "max_rpi": 0.0} for a in ARGS}
misc = []
buf = rest
deadline = time.time() + SECONDS
s.settimeout(2.0)

def frames():
    global buf
    while True:
        if len(buf) < 2:
            return
        b0, b1 = buf[0], buf[1]
        ln = b1 & 0x7F
        off = 2
        if ln == 126:
            if len(buf) < 4: return
            ln = struct.unpack(">H", buf[2:4])[0]; off = 4
        elif ln == 127:
            if len(buf) < 10: return
            ln = struct.unpack(">Q", buf[2:10])[0]; off = 10
        if len(buf) < off + ln:
            return
        payload = buf[off:off + ln]
        buf = buf[off + ln:]
        yield payload

while time.time() < deadline:
    try:
        chunk = s.recv(65536)
    except (socket.timeout, TimeoutError):
        continue
    except Exception as exc:
        misc.append(f"recv: {type(exc).__name__}: {exc}")
        break
    if not chunk:
        misc.append("соединение закрыто биржей")
        break
    buf += chunk
    for payload in frames():
        try:
            msg = json.loads(payload.decode())
        except Exception:
            continue
        if msg.get("op") or msg.get("success") is not None:
            misc.append(json.dumps(msg, ensure_ascii=False))
            continue
        topic = msg.get("topic", "?")
        st = stats.get(topic)
        if st is None:
            misc.append(f"чужой топик: {topic}")
            continue
        st["msgs"] += 1
        typ = msg.get("type")
        st["snap" if typ == "snapshot" else "delta"] += 1
        data = msg.get("data")
        if isinstance(data, list):
            data = data[0] if data else {}
        for side in ("b", "a"):
            for row in (data or {}).get(side) or []:
                if len(row) < 3:
                    continue
                vis, rpi = float(row[1]), float(row[2])
                st["lv"] += 1
                st["vis_sum"] += vis
                st["rpi_sum"] += rpi
                if rpi > 0:
                    st["rpi_pos"] += 1
                    st["max_rpi"] = max(st["max_rpi"], rpi)

s.close()
print()
for a in ARGS:
    st = stats[a]
    print(f"--- {a}")
    print(f"    сообщений {st['msgs']} (snapshot {st['snap']}, delta {st['delta']})")
    print(f"    уровней с числом RPI>0: {st['rpi_pos']} из {st['lv']}")
    print(f"    сумма видимого размера {st['vis_sum']:.2f}, сумма RPI {st['rpi_sum']:.2f},"
          f" максимум RPI {st['max_rpi']:.4f}")
print()
for m in misc[:10]:
    print("служебное:", m)
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("139.99.91.22", username="ubuntu",
          key_filename=os.path.expanduser("~/.ssh/id_rsa"),
          timeout=25, allow_agent=False, look_for_keys=False)
cmd = "cat > /tmp/rpi_probe.py <<'PYEOF'\n" + REMOTE + "\nPYEOF\npython3 /tmp/rpi_probe.py"
_, out, err = c.exec_command(cmd, timeout=180)
print(out.read().decode(errors="replace"))
e = err.read().decode().strip()
if e:
    print("STDERR:", e[:600])
c.close()
