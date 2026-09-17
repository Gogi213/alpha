"""Сколько монет боевого пула имеют живую RPI-ликвидность.

Читает instruments.csv, подписывается на orderbook.rpi.<SYM> по всем символам
(батчами по 10) на одном соединении и считает по каждому символу: сообщений,
уровней, уровней с RPI>0, суммы видимого размера и RPI.
"""
import os
import paramiko

sym_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "instruments.csv")
symbols = []
with open(sym_path, encoding="utf-8") as fh:
    for line in fh:
        line = line.strip()
        if not line or line.startswith("#") or line.startswith("symbol,"):
            continue
        symbols.append(line.split(",")[0].strip())
print(f"символов в пуле: {len(symbols)}")

REMOTE = r'''
import base64, json, os, random, socket, ssl, struct, time

SYMS = os.environ["RPI_SYMS"].split(",")
SECONDS = float(os.environ.get("RPI_SECONDS", "20"))

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
_, buf = buf.split(b"\r\n\r\n", 1)

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

for i in range(0, len(SYMS), 10):
    batch = [f"orderbook.rpi.{x}" for x in SYMS[i:i + 10]]
    send_text(json.dumps({"op": "subscribe", "args": batch}))

zero = {x: {"msgs": 0, "lv": 0, "pos": 0, "vis": 0.0, "rpi": 0.0,
            "slv": 0, "spos": 0, "svis": 0.0, "srpi": 0.0} for x in SYMS}
s.settimeout(2.0)
deadline = time.time() + SECONDS
failed = []

def frames():
    global buf
    while True:
        if len(buf) < 2:
            return
        ln = buf[1] & 0x7F
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
        chunk = s.recv(262144)
    except (socket.timeout, TimeoutError):
        continue
    if not chunk:
        break
    buf += chunk
    for payload in frames():
        if payload[:1] not in (b"{",):
            continue
        try:
            msg = json.loads(payload.decode())
        except Exception:
            continue
        topic = msg.get("topic")
        if not topic:
            if msg.get("success") is False:
                failed.append(json.dumps(msg, ensure_ascii=False)[:160])
            continue
        sym = topic.rsplit(".", 1)[-1]
        st = zero.get(sym)
        if st is None:
            continue
        st["msgs"] += 1
        is_snapshot = msg.get("type") == "snapshot"
        data = msg.get("data")
        if isinstance(data, list):
            data = data[0] if data else {}
        for side in ("b", "a"):
            for row in (data or {}).get(side) or []:
                if len(row) < 3:
                    continue
                vis = float(row[1]); rpi = float(row[2])
                st["lv"] += 1
                st["vis"] += vis
                st["rpi"] += rpi
                if rpi > 0:
                    st["pos"] += 1
                if is_snapshot:
                    st["slv"] += 1
                    st["svis"] += vis
                    st["srpi"] += rpi
                    if rpi > 0:
                        st["spos"] += 1
s.close()

live = [(k, v) for k, v in zero.items() if v["rpi"] > 0]
total_lv = sum(v["lv"] for v in zero.values())
total_pos = sum(v["pos"] for v in zero.values())
snap_lv = sum(v["slv"] for v in zero.values())
snap_pos = sum(v["spos"] for v in zero.values())
snap_vis = sum(v["svis"] for v in zero.values())
snap_rpi = sum(v["srpi"] for v in zero.values())
print(f"символов с сообщениями: {sum(1 for v in zero.values() if v['msgs'] > 0)} из {len(SYMS)}")
print(f"по всем обновлениям: уровней {total_lv}, с RPI>0 {total_pos}"
      f" ({100.0 * total_pos / total_lv:.1f}%)")
print(f"по СНАПШОТАМ (реально стоящий стакан): уровней {snap_lv}, с RPI>0 {snap_pos}"
      f" ({100.0 * snap_pos / snap_lv:.1f}%)")
print(f"по снапшотам в сумме: видимый размер {snap_vis:.1f}, RPI {snap_rpi:.1f},"
      f" RPI/видимый {snap_rpi / snap_vis:.3f}")
print(f"символов с живой RPI: {len(live)}")
print("symbol,msgs,rpi_levels,vis_sum,rpi_sum,snap_lv,snap_pos,snap_vis,snap_rpi")
for k, v in sorted(live, key=lambda kv: -kv[1]["srpi"]):
    ratio = (v["srpi"] / v["svis"]) if v["svis"] > 0 else float("inf")
    print(f"{k},{v['msgs']},{v['pos']},{v['vis']:.1f},{v['rpi']:.1f},"
          f"{v['slv']},{v['spos']},{v['svis']:.1f},{v['srpi']:.1f}")
for line in failed[:5]:
    print("отказ подписки:", line)
'''

c = paramiko.SSHClient()
c.set_missing_host_key_policy(paramiko.AutoAddPolicy())
c.connect("139.99.91.22", username="ubuntu",
          key_filename=os.path.expanduser("~/.ssh/id_rsa"),
          timeout=25, allow_agent=False, look_for_keys=False)
cmd = ("export RPI_SYMS='" + ",".join(symbols) + "'; export RPI_SECONDS=20; "
       "cat > /tmp/rpi_pool.py <<'PYEOF'\n" + REMOTE + "\nPYEOF\npython3 /tmp/rpi_pool.py")
_, out, err = c.exec_command(cmd, timeout=300)
print(out.read().decode(errors="replace"))
e = err.read().decode().strip()
if e:
    print("STDERR:", e[:600])
c.close()
