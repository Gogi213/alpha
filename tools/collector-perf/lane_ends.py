#!/usr/bin/env python3
"""Разбор концов линий (сессий) за N часов по `events-*.jsonl` четырёх боевых коллекторов: кто инициатор — МЫ (замена по pong-таймауту при живых данных, замена по лагу, смена на лучший путь,
возрастная пересмена, накат/перезапуск), БИРЖА (кадр Close, RST, FIN без закрывающего кадра) или СЕТЬ (молчание с ретрансмитами). Для каждого класса — число, простой линии (секунды до следующего
`subscribed` той же линии; у замены с перекрытием простоя нет) и для замен — сколько секунд линия была «испорчена» до замены.
usage: lane_ends.py [часов=24] [--json файл] [--peer-detail]; запускать на srv под systemd-run (читает файлы событий, ничего не пишет в боевые каталоги)."""
import collections
import glob
import json
import statistics
import sys
import time

H = float(sys.argv[1]) if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else 24.0
JSON_OUT = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
DETAIL = "--peer-detail" in sys.argv
ROOTS = {"binance": "/data/collect/binance/data", "bybit": "/data/turbo-probe/soak-bybit/data", "okx": "/data/collect/okx/data", "bingx": "/data/collect/bingx/data"}
if "--roots" in sys.argv:   # JSON {метка: каталог данных} вместо боевых (аудит-руки)
    ROOTS = json.load(open(sys.argv[sys.argv.index("--roots") + 1]))
now = time.time()
lo = now - H * 3600


def lane_of(s):
    return (s // 1_000_000) - 1794


def classify_error(err):
    e = err or ""
    if "Connection reset by peer" in e:
        return "PEER/RST"
    if "Connection reset without closing handshake" in e or "ResetWithoutClosingHandshake" in e:
        return "PEER/FIN без закрывающего кадра"
    if "Broken pipe" in e and ("heartbeat" in e or "ping" in e):
        return "PEER/запись в закрытый сокет (heartbeat/ping)"
    if "Broken pipe" in e:
        return "PEER/запись в закрытый сокет"
    if "timed out" in e.lower() or "TimedOut" in e:
        return "СЕТЬ/таймаут чтения"
    return "ПРОЧЕЕ/" + e[:60]


summary = {}
detail = []
for ex, root in ROOTS.items():
    ends = []          # (ts, shard, lane, session, class, info)
    timeouts = {}      # session -> (ts, kind, data_alive, tcp)
    ws_info = {}       # session -> list of ws_close/ws_end/slow_pong dict
    subs = collections.defaultdict(list)   # (shard, lane) -> [ts подписки]
    conns = {}         # session -> ts connected
    stopped = collections.defaultdict(list)
    started = collections.defaultdict(list)
    forced = set()
    for f in glob.glob(root + "/events-*.jsonl"):
        shard = f.split("events-")[1][:-6]
        for ln in open(f, errors="replace"):
            try:
                j = json.loads(ln)
            except Exception:
                continue
            ts = j.get("ts_ms", 0) / 1000
            if ts < lo - 3600:   # запас на соединения, начатые до окна
                continue
            ev = j.get("ev")
            s = j.get("session")
            if ev == "connected" and s is not None:
                conns[s] = ts
            elif ev == "subscribed" and s is not None:
                subs[(shard, lane_of(s))].append(ts)
            elif ev == "timeout" and s is not None:
                timeouts[s] = (ts, j.get("kind"), j.get("data_alive"), j.get("tcp") or {})
            elif ev in ("ws_close", "ws_end") and s is not None:
                ws_info.setdefault(s, []).append((ts, ev, j))
            elif ev == "replace_forced" and s is not None:
                forced.add(s)
            elif ev == "stopped":
                stopped[shard].append(ts)
            elif ev == "started":
                started[shard].append(ts)
            if ts < lo:
                continue
            if ev in ("path_switch", "path_upgrade", "rotated"):
                old = j.get("old_session")
                to = timeouts.get(old)
                if ev == "path_switch":
                    if to and to[1] == "pong_only" and to[2]:
                        cls = "МЫ/замена по pong-таймауту при живых данных"
                    elif to and to[1] == "silent":
                        cls = "СЕТЬ/молчание (нет pong и данных 10 с) → замена"
                    elif to:
                        cls = "МЫ/замена по pong-таймауту (данные не живы)"
                    else:
                        cls = "МЫ/замена по лагу доставки (late_delivery)"
                elif ev == "path_upgrade":
                    cls = "МЫ/смена на лучший путь (был принят медленный)"
                else:
                    cls = "МЫ/возрастная пересмена"
                ends.append((ts, shard, lane_of(old), old, cls, {"deg_s": (ts - to[0]) if to else None, "forced": old in forced, "timeout": to}))
            elif ev == "error" and s is not None:
                info = ws_info.get(s)
                ends.append((ts, shard, lane_of(s), s, classify_error(j.get("err")), {"err": j.get("err"), "entrance": j.get("entrance")}))
            elif ev == "closed" and s is not None:
                ends.append((ts, shard, lane_of(s), s, "PEER/кадр Close", {"info": ws_info.get(s)}))
    # перезапуски (накат): по одному на шард и запуск
    roll = []
    for shard, st in stopped.items():
        for t in st:
            if t >= lo:
                nxt = [x for x in started[shard] if x >= t]
                roll.append((t, shard, (nxt[0] - t) if nxt else None))
    # массовые события: конец линии, у которого в ±30 с есть ещё ≥ 3 концов той же биржи (общая причина — сеть/сторона биржи/перезапуск), против одиночных
    ts_all = sorted(e[0] for e in ends)
    def mass(ts):
        import bisect
        return bisect.bisect_right(ts_all, ts + 30) - bisect.bisect_left(ts_all, ts - 30) - 1 >= 3
    # простой линии до следующей подписки
    by_class = collections.defaultdict(list)
    for ts, shard, lane, s, cls, info in ends:
        nxt = [x for x in subs.get((shard, lane), []) if x > ts]
        overlap = cls.startswith("МЫ/") or cls.startswith("СЕТЬ/молчание")
        down = 0.0 if overlap else ((nxt[0] - ts) if nxt else None)
        age = (ts - conns[s]) if s in conns else None
        by_class[cls].append({"ts": ts, "shard": shard, "lane": lane, "session": s, "down_s": down, "age_s": age, "info": info, "mass": mass(ts)})
    summary[ex] = by_class
    n_roll = len(roll)
    gaps = [r[2] for r in roll if r[2] is not None]
    print(f"\n== {ex}: концы линий за {H:g} ч (подключений {len(conns)}), перезапусков процесса (накаты) по шардам: {n_roll}, пауза до старта медиана {statistics.median(gaps) if gaps else 0:.1f} с")
    print(f"{'класс':62s} {'шт':>4s} {'из них массовых':>16s} {'простой линии, с (сумма / медиана)':>36s} {'возраст сессии, мин (медиана одиночных)':>40s}")
    for cls, rows in sorted(by_class.items(), key=lambda kv: -len(kv[1])):
        downs = [r["down_s"] for r in rows if r["down_s"] is not None]
        ages = [r["age_s"] / 60 for r in rows if r["age_s"] is not None and not r["mass"]]
        nm = sum(1 for r in rows if r["mass"])
        print(f"{cls:62s} {len(rows):4d} {nm:16d} {sum(downs):18.0f} / {statistics.median(downs) if downs else 0:6.1f}  {statistics.median(ages) if ages else float('nan'):38.1f}")
    forced_n = sum(1 for rows in by_class.values() for r in rows if r["info"].get("forced"))
    if forced_n:
        print(f"    из них принудительных замен (replace_forced): {forced_n}")
    po = by_class.get("МЫ/замена по pong-таймауту при живых данных", [])
    if po:
        degs = [r["info"]["deg_s"] for r in po if r["info"].get("deg_s") is not None]
        rtts = [r["info"]["timeout"][3].get("rtt_ms") for r in po if r["info"].get("timeout")]
        recv = [r["info"]["timeout"][3].get("last_data_recv_ms") for r in po if r["info"].get("timeout")]
        retr = [r["info"]["timeout"][3].get("total_retrans") for r in po if r["info"].get("timeout")]
        print(f"    pong-таймаут при живых данных: до замены {statistics.median(degs) if degs else 0:.0f} с (медиана); TCP в момент таймаута: rtt мс {statistics.median([x for x in rtts if x is not None] or [0])}, "
              f"данных не было {statistics.median([x for x in recv if x is not None] or [0])} мс, ретрансмитов всего {statistics.median([x for x in retr if x is not None] or [0])}")
    if DETAIL:
        for cls, rows in by_class.items():
            if cls.startswith("PEER") or cls.startswith("ПРОЧЕЕ"):
                for r in sorted(rows, key=lambda r: r["ts"])[:60]:
                    detail.append((ex, cls, r))

if DETAIL:
    print("\n== подробно по закрытиям биржей (ex, класс, время UTC, шард, линия, возраст мин, простой с, ws_end/ws_close)")
    for ex, cls, r in detail:
        w = r["info"].get("info") if isinstance(r["info"], dict) else None
        w = [(x[1], {k: x[2].get(k) for k in ("how", "code", "reason", "by")}, {k: (x[2].get("tcp") or {}).get(k) for k in ("last_data_recv_ms", "rtt_ms", "unacked", "total_retrans", "rto_ms")}) for x in (w or [])]
        print(ex, cls[:28], time.strftime("%H:%M:%S", time.gmtime(r["ts"])), r["shard"], r["lane"], f"{(r['age_s'] or 0) / 60:.0f}мин", r["down_s"], w[:1])

if JSON_OUT:
    out = {ex: {c: [{k: v for k, v in r.items() if k != "info"} for r in rows] for c, rows in bc.items()} for ex, bc in summary.items()}
    json.dump(out, open(JSON_OUT, "w"), ensure_ascii=False)
