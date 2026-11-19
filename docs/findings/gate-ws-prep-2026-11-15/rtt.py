import socket, ssl, time, json, collections, statistics as st, sys
HOSTS = ["fx-ws.gateio.ws", "fx-ws.gate.com", "fx-ws.gate.io", "fx-ws.gateio.live"]
res = {}
for h in HOSTS:
    ips = set()
    for _ in range(12):
        try:
            for a in socket.getaddrinfo(h, 443, proto=socket.IPPROTO_TCP): ips.add((a[0].name, a[4][0]))
        except Exception as e:
            ips.add(("ERR", repr(e)))
            break
        time.sleep(0.2)
    res[h] = sorted(ips)
    print(h, len(ips), sorted(ips)[:30])
# RTT per IP: 5 connects each, tcp + tls + ws upgrade
ctx = ssl.create_default_context()
out = {}
for h in HOSTS[:1]:
    for fam, ip in res[h]:
        if fam != "AF_INET": continue
        t = []
        for _ in range(5):
            try:
                t0 = time.perf_counter(); s = socket.create_connection((ip, 443), timeout=8); t1 = time.perf_counter(); s.close(); t.append((t1 - t0) * 1000)
            except Exception as e: t.append(None)
        out[ip] = t
for ip, t in sorted(out.items(), key=lambda kv: min(x for x in kv[1] if x) if any(kv[1]) else 1e9):
    v = [x for x in t if x]; print(ip, "tcp ms", [round(x, 1) for x in v])
