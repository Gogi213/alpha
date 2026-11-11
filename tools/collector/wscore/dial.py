"""Выбор пути до открытия WebSocket: TCP connect с замером RTT рукопожатия (SYN→SYN-ACK) на том сокете, который затем уйдёт в websockets.
Делает: dial(url, max_rtt_ms) -> (sock, rtt_ms); sock = None, если RTT выше порога: путь с потерями, класс пути задаёт порт источника — новый dial даёт другой порт.
Не знает биржу, подписку и формат; TLS и upgrade делает session. Отбраковка за один RTT вместо 15 с проверки лагом (path.PathProbe остаётся второй линией).
"""
import asyncio
import socket
import struct
import time
from urllib.parse import urlsplit

CONNECT_RTT_MAX_MS = 300  # назначаемое (Судье): замер 12.11 (30 соед. srv→Токио): RTT ≤ 276 мс — лаг p99 ≤ 1,5 с, ≥ 320 мс — p99 ≥ 1,9 с, без исключений
TCP_INFO_RTT_OFFSET = 68  # struct tcp_info: tcpi_rtt, мкс (Linux; сразу после connect — замер SYN→SYN-ACK, без задержки цикла событий)


def hostname(url):
    return urlsplit(url).hostname


def kernel_rtt_ms(sock):
    try:
        return struct.unpack_from("I", sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104), TCP_INFO_RTT_OFFSET)[0] / 1000
    except (AttributeError, OSError):  # не Linux
        return None


async def dial(url, max_rtt_ms=CONNECT_RTT_MAX_MS):
    """Открытый TCP-сокет до хоста url и его RTT, мс; (None, rtt) — путь отбракован (max_rtt_ms=None — не отбраковывать)."""
    u = urlsplit(url)
    loop = asyncio.get_running_loop()
    fam, typ, proto, _, addr = (await loop.getaddrinfo(u.hostname, u.port or (443 if u.scheme == "wss" else 80), type=socket.SOCK_STREAM))[0]
    sock = socket.socket(fam, typ, proto)
    sock.setblocking(False)
    t0 = time.monotonic()
    try:
        await loop.sock_connect(sock, addr)
    except BaseException:
        sock.close()
        raise
    rtt = kernel_rtt_ms(sock)
    if rtt is None:
        rtt = (time.monotonic() - t0) * 1000
    if max_rtt_ms is not None and rtt > max_rtt_ms:
        sock.close()
        return None, rtt
    return sock, rtt
