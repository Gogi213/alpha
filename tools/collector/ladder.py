"""TK-531/TK-533: одно соединение WS биржи (binance|bybit), n потоков одного типа, окно DUR с; лаг/обрывы/нагрузка -> json.
Запуск: ladder.py <вид> <n> <top|mid> <dur_с> <out.json> [binance|bybit] [endpoint]; биржа по умолчанию binance; endpoint (Binance: ws | public/ws | market/ws)
можно дать и вместо биржи (прежний вызов TK-531). Binance: book — /ws, agg/mark — /market/ws.
Путь до биржи — общий wscore.dial (без копии): тот же адрес, что у боевого коллектора. kind=all (Bybit): n потоков поровну book/trade/tick на n/3 символов.
Первые 5 с после подписки (снапшот) не считаются; ответы биржи на подписку (не данные) сохраняются в `ctl` — чтобы «0 сообщений» не оставалось без причины."""
import asyncio, json, sys, time, os, socket, ssl, resource, urllib.request
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import websockets
from wscore.dial import dial, peer_ip, tcp_state

EXCH = {
    "binance": {"host": "fstream.binance.com", "batch": 50, "pause": 0.25,
                "url": {"book": "wss://fstream.binance.com/ws", "agg": "wss://fstream.binance.com/market/ws", "mark": "wss://fstream.binance.com/market/ws"},
                "topic": {"book": "{s}@bookTicker", "agg": "{s}@aggTrade", "mark": "{s}@markPrice@1s"}},
    "bybit": {"host": "stream.bybit.com", "batch": 100, "pause": 0.2,  # как в проде bbws: пачка 100 топиков, пауза 0,2 с
              "url": {k: "wss://stream.bybit.com/v5/public/linear" for k in ("book", "trade", "tick", "all", "deep")},
              "topic": {"book": "orderbook.1.{S}", "trade": "publicTrade.{S}", "tick": "tickers.{S}", "deep": "orderbook.50.{S}"}},  # deep — нагрузочный вид (глубина 50, В-115: в данные не пишется): потолок соединения, когда трёх рабочих видов не хватает
}

def pct(xs, q):
    xs = sorted(xs); return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None

def get(u):
    return json.load(urllib.request.urlopen(urllib.request.Request(u, headers={"User-Agent": "alpha-ladder"}), timeout=20))

def symbols(ex):
    if ex == "bybit":
        info = get("https://api.bybit.com/v5/market/instruments-info?category=linear&limit=1000")["result"]["list"]
        ok = {s["symbol"] for s in info if s["status"] == "Trading" and s["contractType"] == "LinearPerpetual" and s["quoteCoin"] == "USDT"}
        t = get("https://api.bybit.com/v5/market/tickers?category=linear")["result"]["list"]
        return [x["symbol"] for x in sorted((x for x in t if x["symbol"] in ok), key=lambda x: -float(x["turnover24h"]))]
    t = get("https://fapi.binance.com/fapi/v1/ticker/24hr")
    info = get("https://fapi.binance.com/fapi/v1/exchangeInfo")
    ok = {s["symbol"] for s in info["symbols"] if s["status"] == "TRADING" and s["contractType"] == "PERPETUAL" and s["quoteAsset"] == "USDT"}
    return [x["symbol"] for x in sorted((x for x in t if x["symbol"] in ok), key=lambda x: -float(x["quoteVolume"]))]

def sub_msgs(ex, streams, i):
    if ex == "bybit":
        return {"op": "subscribe", "args": streams[i:i + EXCH[ex]["batch"]]}
    return {"method": "SUBSCRIBE", "params": streams[i:i + EXCH[ex]["batch"]], "id": i + 1}

async def pinger(ws):
    while True:
        await asyncio.sleep(20)
        await ws.send('{"op":"ping"}')

async def run(kind, n, group, dur, out, ex="binance", endpoint=None):
    cfg = EXCH[ex]; url = f"wss://{cfg['host']}/{endpoint}" if endpoint else cfg["url"][kind]
    syms = symbols(ex)
    per = n // 3 if kind == "all" else n  # all: n потоков = n/3 символов × 3 вида
    start = 0 if group == "top" else min(150, max(0, len(syms) - per))
    chosen = syms[start:start + per]
    kinds = ("book", "trade", "tick") if kind == "all" else (kind,)
    streams = [cfg["topic"][k].format(s=s.lower(), S=s) for s in chosen for k in kinds]
    res = {"exch": ex, "kind": kind, "n": n, "group": group, "dur": dur, "t_start": time.time(), "streams": len(streams), "pool": len(syms), "url": url, "disconnects": 0, "events": [], "ctl": []}
    lags, cnt, nbytes = [], 0, 0
    per_sec = {}
    cpu0 = time.process_time(); w0 = time.monotonic()
    sock = None
    for _ in range(12):
        sock, rtt = await dial(url)
        if sock: break
    res["rtt_ms"] = round(rtt)
    res["ip"] = peer_ip(sock) if sock else None  # семейство адреса (v4/v6) в итоге: TK-532, путь IPv6 терял пакеты
    if sock is None:
        res["error"] = "no_fast_path"; json.dump(res, open(out, "w")); return
    t_end = None
    try:
        async with websockets.connect(url, sock=sock, server_hostname=cfg["host"], max_size=None, ping_interval=None, compression=None, close_timeout=1) as ws:
            pt = asyncio.create_task(pinger(ws)) if ex == "bybit" else None
            for i in range(0, len(streams), cfg["batch"]):
                await ws.send(json.dumps(sub_msgs(ex, streams, i)))
                await asyncio.sleep(cfg["pause"])
            t0 = time.monotonic(); res["t_sub_done_s"] = round(t0 - w0, 2)
            t_end = t0 + dur + 5
            while time.monotonic() < t_end:
                try:
                    m = await asyncio.wait_for(ws.recv(), timeout=max(0.1, t_end - time.monotonic()))
                except asyncio.TimeoutError:
                    break
                now = time.time(); mono = time.monotonic()
                try: d = json.loads(m)
                except Exception: continue
                if ("topic" not in d) if ex == "bybit" else ("result" in d or "error" in d or "code" in d):  # pong/ответ на подписку, не данные
                    if len(res["ctl"]) < 20: res["ctl"].append(m[:200])
                    continue
                if mono - t0 < 5: continue  # снапшот подписки
                E = d.get("ts") if ex == "bybit" else d.get("E")
                nbytes += len(m); cnt += 1
                sec = int(mono - t0); a = per_sec.setdefault(sec, [0, 0]); a[0] += 1; a[1] += len(m)
                if E: lags.append(now * 1000 - E)
            res["tcp"] = tcp_state(sock)  # retransmits/total_retrans за окно — потери пути отдельно от лимитов биржи
    except Exception as e:
        res["disconnects"] += 1; res["events"].append(repr(e)[:200])
    el = max(1e-9, (time.monotonic() - w0) - 5 - res.get("t_sub_done_s", 0))
    res.update(msgs=cnt, kb=round(nbytes / 1024), msg_s=round(cnt / dur, 1), kb_s=round(nbytes / 1024 / dur, 1),
               lag_p50=pct(lags, .5), lag_p90=pct(lags, .9), lag_p99=pct(lags, .99), lag_max=max(lags) if lags else None,
               lag_first_p50=pct(lags[:len(lags) // 3], .5), lag_last_p50=pct(lags[-(len(lags) // 3):], .5),
               silent_s=sum(1 for s in range(5, dur + 5) if s not in per_sec), peak_msg_s=max((v[0] for v in per_sec.values()), default=0),
               cpu_s=round(time.process_time() - cpu0, 2), t_end=time.time())
    json.dump(res, open(out, "w"))

if __name__ == "__main__":
    k, n, g, dur, out = sys.argv[1], int(sys.argv[2]), sys.argv[3], int(sys.argv[4]), sys.argv[5]
    a6 = sys.argv[6] if len(sys.argv) > 6 else "binance"
    ex, ep = (a6, sys.argv[7] if len(sys.argv) > 7 else None) if a6 in EXCH else ("binance", a6)
    asyncio.run(run(k, n, g, dur, out, ex, ep))
