"""connprobe N SEC OUT — предсказывает ли время TCP connect() класс пути соединения: N параллельных соединений btcusdt@bookTicker, у каждого connect_ms (SYN→SYN-ACK) и лаг rx−E за SEC с, тот же сокет."""
import asyncio, json, socket, ssl, struct, subprocess, sys, time
n, sec, out = int(sys.argv[1]), int(sys.argv[2]), sys.argv[3]
host, url = "fstream.binance.com", "wss://fstream.binance.com/ws/btcusdt@bookTicker"

IP = socket.gethostbyname(host)  # один раз: блокирующий DNS внутри корутин искажал connect_ms

def tcpi_rtt_ms(s):
    """tcpi_rtt (сглаженный RTT ядра, смещение 68 в struct tcp_info) — сразу после connect это замер SYN→SYN-ACK."""
    return struct.unpack_from("I", s.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104), 68)[0] / 1000

def ss_rtt(lport):
    r = subprocess.run(["ss", "-tin", f"sport = :{lport}"], capture_output=True, text=True).stdout
    i = r.find(" rtt:")
    return float(r[i + 5:].split("/")[0]) if i >= 0 else None

async def one(k):
    import websockets
    loop = asyncio.get_running_loop()
    await asyncio.sleep(k * 0.15)
    ip = IP
    s = socket.socket(); s.setblocking(False)
    t0 = time.monotonic()
    await loop.sock_connect(s, (ip, 443))
    connect_ms = (time.monotonic() - t0) * 1000
    syn_rtt = tcpi_rtt_ms(s)
    lags = []
    async with websockets.connect(url, sock=s, server_hostname=host, compression=None, ping_interval=None, max_size=None) as ws:
        lport = s.getsockname()[1]
        t0 = time.time()
        async for m in ws:
            rx = time.time()
            if rx - t0 > 4:  # снапшот подписки
                lags.append(rx * 1000 - json.loads(m)["E"])
            if rx - t0 > sec:
                break
        rtt = ss_rtt(lport)
    lags.sort(); q = lambda p: round(lags[min(len(lags) - 1, int(len(lags) * p))])
    return {"k": k, "ip": ip, "connect_ms": round(connect_ms), "syn_rtt_ms": round(syn_rtt), "ss_rtt_ms": rtt, "n": len(lags), "p50": q(.5), "p99": q(.99)}

async def main():
    res = await asyncio.gather(*(one(k) for k in range(n)), return_exceptions=True)
    json.dump([r if isinstance(r, dict) else repr(r) for r in res], open(out, "w"))
asyncio.run(main())
