#!/usr/bin/env python3
"""Аудит обрывов линий (collector-perf, 14.11): сравнение рук D (боевое правило + диагностика), R (`--no-pong-only-degrade`), N (`--no-ping`) по биржам из `/data/perf/audit/<биржа>-<рука>/data`.
Для каждой руки: сколько линий-часов, концов по классам (биржа/сеть/мы), частота на линию-час; для закрытий биржей — состояние нашей стороны в момент закрытия (`diag`): возраст сессии,
секунды с последнего кадра, с последнего пинга биржи, с нашего последнего pong и его задержка, с нашего последнего пинга/pong от биржи, неотправленная запись, непрочитанная очередь, неподтверждённое нашими пакетами;
сколько закрытий объясняет каждый дефект нашей стороны. Рукопожатие (`hs`): версия TLS, ALPN, расширения, сервер.
usage: audit_report.py [--root /data/perf/audit] [--json файл]; читает файлы событий, ничего не пишет в боевые каталоги; запускать на srv под systemd-run."""
import collections
import glob
import json
import statistics
import sys
import time

ROOT = sys.argv[sys.argv.index("--root") + 1] if "--root" in sys.argv else "/data/perf/audit"
JSON_OUT = sys.argv[sys.argv.index("--json") + 1] if "--json" in sys.argv else None
EXCH = ("binance", "bybit", "okx", "bingx")
ARMS = ("D", "R", "N")
# Лимиты биржи на тишину входящих, мс (по документации): сверх — наш heartbeat дефектен. Bybit — рекомендация пинга ≤ 20 с, OKX — разрыв при 30 с без данных, BingX — Pong в ответ на Ping (≈ 5 с).
IDLE_LIMIT_MS = {"binance": 600_000, "bybit": 20_000, "okx": 30_000, "bingx": 30_000}
# Задержка нашего pong на пинг биржи, сверх которой считаем ответ поздним.
PONG_LATE_US = 1_000_000


def pct(v, p):
    v = sorted(v)
    return v[min(len(v) - 1, int(len(v) * p))] if v else None


def fmt(x, nd=0):
    return "-" if x is None else f"{x:.{nd}f}"


def load(ex, arm):
    d = f"{ROOT}/{ex}-{arm}/data"
    ev = []
    for f in glob.glob(d + "/events-*.jsonl"):
        shard = f.split("events-")[1][:-6]
        for ln in open(f, errors="replace"):
            try:
                j = json.loads(ln)
            except Exception:
                continue
            j["_shard"] = shard
            ev.append(j)
    ev.sort(key=lambda j: j.get("ts_ms", 0))
    return ev


def flags(ex, e):
    """Дефекты нашей стороны в момент закрытия по `diag`/`tcp` события."""
    d, t = e.get("diag") or {}, e.get("tcp") or {}
    out = []
    sp, sps = d.get("since_srv_ping_ms"), d.get("since_pong_sent_ms")
    if d.get("srv_pings", 0) > 0 and (d.get("pongs_sent", 0) == 0 or (sp is not None and sps is not None and sps > sp)):
        out.append("пинг биржи без нашего pong")
    if d.get("pong_late", 0) > 0 or (d.get("pong_lat_max_us") or 0) > PONG_LATE_US:
        out.append("pong позже 1 с")
    if (d.get("since_frame_ms") or 0) > IDLE_LIMIT_MS[ex]:
        out.append("тишина входящих сверх лимита биржи")
    if d.get("need_flush"):
        out.append("неотправленная запись")
    if (t.get("recvq") or 0) > 65536:
        out.append("непрочитанная очередь > 64 КиБ")
    if (t.get("unacked") or 0) > 0 or (t.get("total_retrans") or 0) > 0:
        out.append("наши пакеты не подтверждены/ретрансмиты")
    return out


rep = {}
print(f"аудит обрывов: {time.strftime('%F %T', time.gmtime())} UTC, каталог {ROOT}")
for ex in EXCH:
    rep[ex] = {}
    print(f"\n==== {ex} ====")
    hs0 = None
    rows = []
    for arm in ARMS:
        ev = load(ex, arm)
        if not ev:
            continue
        t0 = min((j["ts_ms"] for j in ev if j.get("ev") == "started"), default=ev[0]["ts_ms"]) / 1000
        t1 = ev[-1]["ts_ms"] / 1000
        try:   # события редки: конец окна — по последней записи health
            t1 = max(t1, json.load(open(f"{ROOT}/{ex}-{arm}/health.json"))["ts_ms"] / 1000)
        except Exception:
            pass
        conns = {(j["_shard"], j["session"]): j["ts_ms"] / 1000 for j in ev if j.get("ev") == "connected"}   # номер сессии общий для шардов
        n_lanes = len({(j["_shard"], (j["session"] // 1_000_000) - 1794) for j in ev if j.get("ev") == "connected"}) or 6
        lane_h = max(n_lanes * (t1 - t0) / 3600, 1e-9)
        for j in ev:
            if j.get("ev") == "connected" and hs0 is None and j.get("hs"):
                hs0 = (j["hs"], j.get("ip"), j.get("entrance"), j.get("url"))
        cnt = collections.Counter(j.get("ev") for j in ev)
        tmo = collections.Counter((j.get("kind"), bool(j.get("data_alive"))) for j in ev if j.get("ev") == "timeout")
        ends = [j for j in ev if j.get("ev") in ("ws_close", "ws_end") and j.get("how") != "closed_after_handshake"]
        peer = collections.Counter()
        ages, fl = [], collections.Counter()
        pong_us, silent_before = [], []
        detail = []
        for j in ends:
            kind = "Close-кадр" if j["ev"] == "ws_close" else j.get("how", "?")
            peer[kind] += 1
            d = j.get("diag") or {}
            age = d.get("age_s")
            if age is not None:
                ages.append(age / 60)
            if d.get("pong_lat_max_us") is not None:
                pong_us.append(d["pong_lat_max_us"])
            fs = flags(ex, j)
            for f in fs:
                fl[f] += 1
            detail.append((j["ts_ms"] / 1000, j["_shard"], (j["session"] // 1_000_000) - 1794, kind, j, fs))
        slow = [j for j in ev if j.get("ev") == "slow_pong"]
        slow_rtt = [j["rtt_ms"] for j in slow if "rtt_ms" in j]
        k2r = [j["k2r_us"] for j in slow if "k2r_us" in j]
        n_end = len(ends)
        rep[ex][arm] = {"lane_h": lane_h, "conns": len(conns), "ends": dict(peer), "peer_end": n_end, "ping_degraded": cnt["ping_degraded"], "timeouts": {f"{k[0]}/{'alive' if k[1] else 'dead'}": v for k, v in tmo.items()},
                        "slow_pong": len(slow), "flags": dict(fl), "ages_min": ages}
        print(f"\n-- рука {arm}: окно {(t1 - t0) / 3600:.2f} ч, линий {n_lanes}, линий-часов {lane_h:.1f}, подключений {len(conns)} (повторных {max(len(conns) - n_lanes, 0)}), "
              f"ping_degraded {cnt['ping_degraded']}, path_switch {cnt['path_switch']}, rotated {cnt['rotated']}, замен-принуд. {cnt['replace_forced']}, ошибок {cnt['error']}, Close {cnt['closed']}")
        print(f"   таймауты pong: {dict(((f'{k[0]}/' + ('данные живы' if k[1] else 'данных нет')), v) for k, v in tmo.items())}; slow_pong (≥ порога) {len(slow)}"
              f"{f', rtt мс p50/p90/max {pct(slow_rtt, .5):.0f}/{pct(slow_rtt, .9):.0f}/{max(slow_rtt):.0f}' if slow_rtt else ''}"
              f"{f', ядро→чтение мкс p50/max {pct(k2r, .5):.0f}/{max(k2r)}' if k2r else ''}")
        print(f"   закрытия биржей/ошибки чтения: {n_end} ({n_end / lane_h:.2f} на линию-час) {dict(peer)}")
        if ages:
            print(f"   возраст сессии при закрытии, мин: p10/p50/p90/max {pct(ages, .1):.1f}/{pct(ages, .5):.1f}/{pct(ages, .9):.1f}/{max(ages):.1f}")
        if pong_us:
            print(f"   наш pong на пинг биржи (ядерная метка → после записи), мкс: p50/p90/max {pct(pong_us, .5):.0f}/{pct(pong_us, .9):.0f}/{max(pong_us)}")
        if n_end:
            print(f"   дефекты нашей стороны при закрытиях: " + (", ".join(f"{k} — {v} из {n_end}" for k, v in fl.most_common()) or "нет"))
        for ts, shard, lane, kind, j, fs in detail[:40]:
            d = j.get("diag") or {}
            t = j.get("tcp") or {}
            print(f"     {time.strftime('%H:%M:%S', time.gmtime(ts))} {shard:7s} л{lane} {kind[:26]:26s} возраст {d.get('age_s', 0) / 60:5.1f} мин | с кадра {d.get('since_frame_ms')} мс, "
                  f"с пинга бирж. {d.get('since_srv_ping_ms')} мс (всего {d.get('srv_pings')}), с нашего pong {d.get('since_pong_sent_ms')} мс (макс. задержка {d.get('pong_lat_max_us')} мкс), "
                  f"наш пинг {d.get('since_our_ping_ms')} мс / pong бирж. {d.get('since_our_pong_ms')} мс | recvq {t.get('recvq')}, unacked {t.get('unacked')}, ретр. {t.get('total_retrans')}, "
                  f"посл. данные отпр./подтв. {t.get('last_data_sent_ms')}/{t.get('last_ack_recv_ms')} мс | {','.join(fs) or '-'}")
    if hs0:
        h, ip, ent, url = hs0
        print(f"\n   рукопожатие: {url} вход {ent} ip {ip}: HTTP {h.get('status')}, TLS {h.get('tls', {}).get('version')} {h.get('tls', {}).get('cipher')}, ALPN {h.get('tls', {}).get('alpn')}, "
              f"расширения ws {h.get('ws_ext')}, сервер {h.get('server')}, via {h.get('via')}")

if JSON_OUT:
    json.dump(rep, open(JSON_OUT, "w"), ensure_ascii=False, default=str)
