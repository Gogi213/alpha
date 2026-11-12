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


def parse_entrance(spec):
    """«хост[@ip]» -> (хост, ip или None): вход биржи по имени (SNI/Host) и, если задан, по этому адресу (проба входов, линии с разными входами)."""
    host, _, ip = spec.partition("@")
    return host, ip or None


def with_host(url, host):
    """url с другим именем хоста; порт и путь сохраняются."""
    u = urlsplit(url)
    return u._replace(netloc=host + (f":{u.port}" if u.port else "")).geturl()


def kernel_rtt_ms(sock):
    try:
        return struct.unpack_from("I", sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104), TCP_INFO_RTT_OFFSET)[0] / 1000
    except (AttributeError, OSError):  # не Linux
        return None


# struct tcp_info (Linux, 104 байта): байты 2/4 — retransmits/backoff (ступень RTO), u32 по смещениям: 8 rto, 24 unacked, 32 lost, 36 retrans, 52 last_data_recv, 68 rtt, 100 total_retrans
def tcp_state(sock):
    """Состояние TCP сокета для события таймаута (чем объяснить паузу: потери пути — retransmits/backoff/rto растут, last_data_recv большой; биржа молчит при чистом TCP — они нули); None — не Linux."""
    try:
        b = sock.getsockopt(socket.IPPROTO_TCP, socket.TCP_INFO, 104)
    except (AttributeError, OSError):
        return None
    u = lambda o: struct.unpack_from("I", b, o)[0]  # noqa: E731
    return {"retransmits": b[2], "backoff": b[4], "rto_ms": u(8) // 1000, "unacked": u(24), "lost": u(32), "retrans": u(36), "last_data_recv_ms": u(52), "rtt_ms": u(68) // 1000, "total_retrans": u(100)}


def peer_ip(sock):
    """Адрес удалённой стороны сокета (в событие connected: обрыв группы соединений объясняется общим узлом CDN); None — не получилось."""
    try:
        return sock.getpeername()[0]
    except (AttributeError, OSError, IndexError):
        return None


async def dial(url, max_rtt_ms=CONNECT_RTT_MAX_MS, ip=None):
    """Открытый TCP-сокет до хоста url и его RTT, мс; (None, rtt) — путь отбракован (max_rtt_ms=None — не отбраковывать).
    ip — подключиться к этому адресу, а не к тому, что отдаёт DNS (проба входов: другая точка присутствия CDN при том же имени/SNI)."""
    u = urlsplit(url)
    loop = asyncio.get_running_loop()
    fam, typ, proto, _, addr = (await loop.getaddrinfo(ip or u.hostname, u.port or (443 if u.scheme == "wss" else 80), type=socket.SOCK_STREAM))[0]
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
